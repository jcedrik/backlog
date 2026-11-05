// Draws docs/demo/preview.svg, the picture the README links to the demo with:
// the demo's opening example, one round into decoding.
//   node docs/demo/make_preview.js > docs/demo/preview.svg
const UF = require("./uf.js");

const code = UF.surfaceCode(5);
const errors = [4, 12, 21, 23];
const fired = UF.syndrome(code, errors);
const result = UF.decode(code, fired);
const style = `
  .face.z { fill: #1f3a66; } .face.x { fill: #c9d2dd; } .face.lit { fill: #e8a628; }
  .boundary { stroke: #c9d2dd; stroke-width: 0.06; stroke-dasharray: 0.14 0.11; fill: none; }
  .boundary.reached { stroke: #e8a628; }
  .grow, .under { stroke-linecap: round; stroke-linejoin: round; fill: none; }
  .grow { stroke: #e8a628; stroke-width: 0.15; } .under { stroke: rgba(122, 82, 10, 0.5); stroke-width: 0.22; }
  .qubit .hit, .qubit .ring { fill: none; stroke: none; }
  .qubit .dot { fill: #fff; stroke: #16202d; stroke-width: 0.045; }
  .qubit.error .dot { fill: #cc2f45; stroke: #7d1626; }`;
const svg = UF.renderSVG(code, {
  errors: new Set(errors), lit: new Set(fired), round: result.rounds[1],
  forest: null, faded: null, fixed: new Set(), result: null, phase: 0,
}, { style, static: true });
process.stdout.write(svg.replace("<svg ", '<svg width="420" height="420" ') + "\n");
