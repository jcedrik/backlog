// uf.js: backlog's Union-Find decoder, step by step, on the rotated surface code.
//
// The decoder is a line-for-line port of backlog's C++ reference decoder
// (cpp/src/reference.cpp) with every edge weight 2, the classic unweighted
// Union-Find: synchronous growth rounds, merges, a spanning forest in edge id
// order, peeling from the leaves. tests/test_demo.py checks it edge for edge
// against the C++ on random errors. Here it also records every round, so the
// page can replay it.
//
// Noise: bit flips (X errors) on data qubits, perfect measurements of the Z
// checks. Used by index.html in the browser and by node in the tests.
(function (root) {
  "use strict";

  // Data qubit (r, c), 0 <= r, c < d, is qubit r * d + c. Face (i, j) for
  // -1 <= i, j <= d - 1 covers the qubits (i..i+1, j..j+1) inside the grid.
  // Faces with i + j even are Z checks, the others X checks. Two-qubit faces
  // sit on the edges: Z checks on the top and bottom, X checks on the left and
  // right, so X errors can run off the code only to the left or the right.
  function surfaceCode(d) {
    const faces = [];
    for (let i = -1; i <= d - 1; i++) {
      for (let j = -1; j <= d - 1; j++) {
        const qubits = [];
        for (const [r, c] of [[i, j], [i, j + 1], [i + 1, j], [i + 1, j + 1]]) {
          if (r >= 0 && r < d && c >= 0 && c < d) qubits.push(r * d + c);
        }
        const type = ((i + j) & 1) === 0 ? "Z" : "X";
        const topOrBottom = i === -1 || i === d - 1;
        const side = j === -1 || j === d - 1;
        if (qubits.length === 4 ||
            (qubits.length === 2 && ((topOrBottom && type === "Z") || (side && type === "X")))) {
          faces.push({ i, j, type, qubits });
        }
      }
    }
    // Detectors are the Z checks; node n is the boundary.
    const checks = faces.filter((f) => f.type === "Z");
    const n = checks.length;
    const index = new Map(checks.map((f, k) => [f.i + "," + f.j, k]));
    // One edge per data qubit, with the qubit's index as its id. A qubit in
    // the first or last column touches one Z check: its edge goes to the
    // boundary. The logical observable is the parity of column 0.
    const edges = [];
    for (let r = 0; r < d; r++) {
      for (let c = 0; c < d; c++) {
        const ends = [];
        for (const [i, j] of [[r - 1, c - 1], [r - 1, c], [r, c - 1], [r, c]]) {
          const k = index.get(i + "," + j);
          if (k !== undefined) ends.push(k);
        }
        edges.push({ a: ends[0], b: ends.length === 2 ? ends[1] : n, obs: c === 0 ? 1 : 0, r, c });
      }
    }
    return { d, faces, checks, n, edges };
  }

  // The defects (lit checks) of a set of flipped qubits.
  function syndrome(code, errors) {
    const lit = new Uint8Array(code.n + 1);
    for (const q of errors) {
      lit[code.edges[q].a] ^= 1;
      lit[code.edges[q].b] ^= 1;
    }
    const fired = [];
    for (let v = 0; v < code.n; v++) if (lit[v]) fired.push(v);
    return fired;
  }

  function decode(code, fired) {
    const n = code.n, V = n + 1, B = n, E = code.edges.length, edges = code.edges;
    const defect0 = new Uint8Array(V);
    for (const v of fired) defect0[v] ^= 1;

    // Clusters: disjoint sets with parity and boundary contact at the root.
    const parent = Int32Array.from({ length: V }, (_, i) => i);
    const rank = new Uint8Array(V);
    const parity = Uint8Array.from(defect0);
    const atBoundary = new Uint8Array(V);
    atBoundary[B] = 1;
    const find = (x) => {
      while (parent[x] !== x) {
        parent[x] = parent[parent[x]];
        x = parent[x];
      }
      return x;
    };
    const unite = (x, y) => {
      let rx = find(x), ry = find(y);
      if (rx === ry) return;
      if (rank[rx] < rank[ry]) [rx, ry] = [ry, rx];
      parent[ry] = rx;
      if (rank[rx] === rank[ry]) rank[rx]++;
      parity[rx] ^= parity[ry];
      atBoundary[rx] |= atBoundary[ry];
    };
    const odd = (r) => parity[r] === 1 && atBoundary[r] === 0;

    // Growth, one round at a time. fromA and fromB only record which end
    // grew each half-edge, for drawing; the rule is the reference's.
    const growth = new Uint8Array(E), full = new Uint8Array(E);
    const fromA = new Uint8Array(E), fromB = new Uint8Array(E);
    const fullEdges = [];
    const rounds = [];
    const snapshot = (merged) => {
      const roots = Int32Array.from({ length: V }, (_, v) => find(v));
      const oddRoot = new Uint8Array(V);
      for (let v = 0; v < V; v++) oddRoot[v] = odd(roots[v]) ? 1 : 0;
      rounds.push({ fromA: fromA.slice(), fromB: fromB.slice(), roots, oddRoot, merged });
    };
    snapshot([]);
    for (;;) {
      let anyOdd = false;
      for (let v = 0; v < n && !anyOdd; v++) anyOdd = odd(find(v));
      if (!anyOdd) break;
      const toMerge = [];
      let grew = false;
      for (let e = 0; e < E; e++) {
        if (full[e]) continue;
        const ra = find(edges[e].a), rb = find(edges[e].b);
        if (ra === rb) continue; // internal edges do not grow
        const ga = odd(ra) ? 1 : 0, gb = odd(rb) ? 1 : 0;
        if (ga + gb === 0) continue;
        growth[e] += ga + gb;
        fromA[e] += ga;
        fromB[e] += gb;
        grew = true;
        if (growth[e] >= 2) {
          full[e] = 1;
          toMerge.push(e);
        }
      }
      for (const e of toMerge) {
        unite(edges[e].a, edges[e].b);
        fullEdges.push(e);
      }
      snapshot(toMerge);
      if (!grew) break;
    }

    // Spanning forest of the grown edges, in edge id order (all weights 2).
    fullEdges.sort((x, y) => x - y);
    const fparent = Int32Array.from({ length: V }, (_, i) => i);
    const ffind = (x) => {
      while (fparent[x] !== x) {
        fparent[x] = fparent[fparent[x]];
        x = fparent[x];
      }
      return x;
    };
    const fadj = Array.from({ length: V }, () => []);
    const fdeg = new Int32Array(V);
    const forestNodes = [];
    const forest = [];
    for (const e of fullEdges) {
      const { a, b } = edges[e];
      const ra = ffind(a), rb = ffind(b);
      if (ra === rb) continue;
      fparent[ra] = rb;
      forest.push(e);
      fadj[a].push(e);
      fadj[b].push(e);
      if (fdeg[a]++ === 0) forestNodes.push(a);
      if (fdeg[b]++ === 0) forestNodes.push(b);
    }

    // Peeling: leaves in order of first appearance; the boundary is never peeled.
    const defect = Uint8Array.from(defect0);
    const removed = new Uint8Array(E);
    const leaves = forestNodes.filter((v) => v !== B && fdeg[v] === 1);
    const peel = [];
    const correction = [];
    let flip = 0;
    for (let head = 0; head < leaves.length; head++) {
      const u = leaves[head];
      if (fdeg[u] !== 1) continue;
      const e = fadj[u].find((x) => !removed[x]);
      if (e === undefined) continue;
      const v = edges[e].a === u ? edges[e].b : edges[e].a;
      removed[e] = 1;
      fdeg[u]--;
      fdeg[v]--;
      const fix = defect[u] === 1;
      if (fix) {
        flip ^= edges[e].obs;
        correction.push(e);
        defect[u] = 0;
        if (v !== B) defect[v] ^= 1;
      }
      peel.push({ e, fix });
      if (v !== B && fdeg[v] === 1) leaves.push(v);
    }
    return { rounds, forest, peel, correction, flip };
  }

  // Did error + correction flip the stored qubit? (Parity on column 0.)
  function logicalFlip(code, errors, correction) {
    let p = 0;
    for (const q of errors) p ^= code.edges[q].obs;
    for (const q of correction) p ^= code.edges[q].obs;
    return p === 1;
  }

  // ---- drawing --------------------------------------------------------------
  // Qubit (r, c) sits at x = c, y = r. A check's node sits at the centre of its
  // face (of the half-disc, for a check on the top or bottom edge). An edge is
  // drawn from one node to its qubit and on to the other node, or to the
  // dashed side for a boundary edge: each half is a half-edge.

  function nodePoint(code, v) {
    const f = code.checks[v];
    if (f.i === -1) return [f.j + 0.5, -0.21];
    if (f.i === code.d - 1) return [f.j + 0.5, code.d - 1 + 0.21];
    return [f.j + 0.5, f.i + 0.5];
  }

  // [end a, the qubit, end b] of edge e
  function edgePoints(code, e) {
    const { a, b, r, c } = code.edges[e];
    const pb = b === code.n ? [c === 0 ? -0.5 : code.d - 0.5, r] : nodePoint(code, b);
    return [nodePoint(code, a), [c, r], pb];
  }

  function facePath(code, f) {
    const d = code.d;
    const fx = (x) => +x.toFixed(3);
    if (f.qubits.length === 4) {
      return `M${f.j} ${f.i}h1v1h-1z`;
    }
    if (f.i === -1) return `M${f.j} 0A0.5 0.5 0 0 1 ${f.j + 1} 0z`;
    if (f.i === d - 1) return `M${f.j} ${d - 1}A0.5 0.5 0 0 0 ${f.j + 1} ${d - 1}z`;
    if (f.j === -1) return `M0 ${f.i}A0.5 0.5 0 0 0 0 ${fx(f.i + 1)}z`;
    return `M${d - 1} ${f.i}A0.5 0.5 0 0 1 ${d - 1} ${f.i + 1}z`;
  }

  const pt = (p) => `${+p[0].toFixed(3)} ${+p[1].toFixed(3)}`;
  const line = (...ps) => "M" + ps.map(pt).join("L");

  // view: { errors: Set of qubits, lit: Set of checks, round: decode round or
  // null, forest: edges or null, faded: Set of peeled edges, fixed: Set of
  // correction qubits, result: null | "ok" | "fail", phase: seconds for the
  // pulse, focus: qubit to label }.
  function renderSVG(code, view, opts = {}) {
    const d = code.d;
    const pad = 0.95;
    const out = [];
    const w = d - 1 + 2 * pad, h = d - 1 + 2 * pad;
    out.push(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="${-pad} ${-pad} ${w} ${h}"` +
             ` class="lattice" role="img" aria-label="Distance-${d} surface code">`);
    if (opts.style) out.push(`<style>${opts.style}</style>`);
    const round = view.round;
    const pulse = `animation-delay:-${(view.phase || 0).toFixed(2)}s`;

    // the open boundaries, left and right, lit once a cluster reaches them
    const reached = [false, false];
    if (round) {
      code.edges.forEach((ed, e) => {
        if (ed.b === code.n && round.fromA[e] + round.fromB[e] >= 2) reached[ed.c === 0 ? 0 : 1] = true;
      });
    }
    out.push(`<g class="sides">` +
             `<path class="boundary${reached[0] ? " reached" : ""}" d="M-0.5 -0.5V${d - 0.5}"/>` +
             `<path class="boundary${reached[1] ? " reached" : ""}" d="M${d - 0.5} -0.5V${d - 0.5}"/></g>`);

    // checks
    out.push(`<g class="faces">`);
    code.faces.forEach((f) => {
      let cls = f.type === "Z" ? "face z" : "face x";
      if (f.type === "Z") {
        const v = code.checks.indexOf(f);
        if (view.lit.has(v)) {
          cls += " lit";
          if (round && round.oddRoot[v]) cls += " odd";
        }
      }
      out.push(`<path class="${cls}" d="${facePath(code, f)}" style="${pulse}"/>`);
    });
    out.push(`</g>`);

    // growth: half-edges from the end that grew them, over a darker underlay
    // so that they read on a lit check too
    if (round) {
      const paths = [];
      for (let e = 0; e < code.edges.length; e++) {
        const ga = round.fromA[e], gb = round.fromB[e];
        if (!ga && !gb) continue;
        if (view.faded && view.faded.has(e)) continue;
        const [pa, pq, pb] = edgePoints(code, e);
        const inForest = view.forest && view.forest.has(e);
        const oddA = round.oddRoot[code.edges[e].a], oddB = round.oddRoot[code.edges[e].b];
        const cls = (k) => (k ? " odd" : "") + (inForest ? " tree" : "") +
                           (view.forest && !inForest ? " spare" : "");
        if (ga + gb >= 2) paths.push([cls(oddA || oddB), line(pa, pq, pb)]);
        else if (ga) paths.push([cls(oddA), line(pa, pq)]);
        else paths.push([cls(oddB), line(pb, pq)]);
      }
      out.push(`<g class="growth">`);
      for (const [c, p] of paths) out.push(`<path class="under${c}" d="${p}" style="${pulse}"/>`);
      for (const [c, p] of paths) out.push(`<path class="grow${c}" d="${p}" style="${pulse}"/>`);
      out.push(`</g>`);
    }

    // the result: error + correction, loops or a chain across the code
    if (view.result) {
      out.push(`<g class="residue ${view.result}">`);
      for (let q = 0; q < code.edges.length; q++) {
        if (view.errors.has(q) !== view.fixed.has(q)) {
          out.push(`<path d="${line(...edgePoints(code, q))}"/>`);
        }
      }
      out.push(`</g>`);
    }

    // data qubits
    out.push(`<g class="qubits">`);
    for (let q = 0; q < code.edges.length; q++) {
      const { r, c } = code.edges[q];
      let cls = "qubit";
      if (view.errors.has(q)) cls += " error";
      if (view.fixed && view.fixed.has(q)) cls += " fixed";
      out.push(`<g class="${cls}" data-q="${q}" tabindex="${opts.static ? -1 : 0}" role="button"` +
               ` aria-pressed="${view.errors.has(q)}" aria-label="Qubit ${r + 1}, ${c + 1}">` +
               `<circle class="hit" cx="${c}" cy="${r}" r="0.42"/>` +
               `<circle class="ring" cx="${c}" cy="${r}" r="0.27"/>` +
               `<circle class="dot" cx="${c}" cy="${r}" r="0.17"/></g>`);
    }
    out.push(`</g></svg>`);
    return out.join("");
  }

  const UF = { surfaceCode, syndrome, decode, logicalFlip, edgePoints, renderSVG };
  if (typeof module !== "undefined" && module.exports) module.exports = UF;
  else root.UF = UF;
})(typeof globalThis !== "undefined" ? globalThis : this);
