# backlog

**Un décodeur de correction d'erreurs quantiques temps réel, bâti de zéro :
de la simulation jusqu'au FPGA.**

[English version](README.md)

![PyMatching contre Union-Find de backlog, pondéré et non pondéré, surface code rotatif sous bruit au niveau circuit](results/threshold_comparison.png)

## Pourquoi « backlog »

Un ordinateur quantique tolérant aux fautes doit décoder les erreurs plus vite
qu'elles apparaissent. Si le décodeur est plus lent que le flux de syndromes,
le retard s'accumule et le calcul ralentit de façon exponentielle. Sur une
puce supraconductrice, un cycle de correction dure environ 1 microseconde :
le décodeur a donc un budget temps réel strict. Ce projet construit un
décodeur pour ce problème, le mesure honnêtement à chaque étape, et montre le
retard qui s'accumule quand le décodeur décroche.

## Résultats

Mémoire sur surface code rotatif, bruit au niveau circuit généré par Stim.
Tous les décodeurs passent par sinter sur les mêmes circuits : 12,8 millions
de tirages, 96 points, au moins 68 erreurs logiques par point. Les barres
d'erreur valent un écart-type.

| | PyMatching 2 (MWPM) | UF backlog, pondéré | UF backlog, non pondéré |
|---|---|---|---|
| croisement de d = 7 et d = 9 | 0,75 ± 0,02 % | 0,77 ± 0,03 % | 0,66 ± 0,04 % |
| taux d'erreur logique relatif à PyMatching, d = 3 et 5 | 1 | 0,96 à 1,11 | 1,16 à 2,08 |
| taux d'erreur logique relatif à PyMatching, d = 7 et 9 | 1 | 1,05 à 1,20 | 1,61 à 3,66 |
| vitesse relative à PyMatching, d = 3 | 1 | 0,82 à 0,87 | |
| vitesse relative à PyMatching, d = 5 à 13, p = 0,1 % | 1 | 1,02 à 1,19 | |
| vitesse relative à PyMatching, d = 5 à 13, p = 0,3 % | 1 | 0,98 à 1,04 | |

- **C'est la pondération qui rend Union-Find compétitif.** Faire croître
  chaque arête selon sa log-vraisemblance fait passer Union-Find de jusqu'à
  3,7 fois le taux d'erreur logique de PyMatching à 0 à 20 % de plus, avec le
  même seuil à l'intérieur des barres d'erreur.
- **Vitesse : à égalité avec PyMatching 2, pas une raclée.** Sur un cœur, ce
  décodeur est 15 à 19 % plus rapide à p = 0,1 % à partir de d = 9, à égalité
  à p = 0,3 %, et plus lent à d = 3 (les reprises concordent à environ 5 %
  près).
- **Le décodage en flux continu marche, jusqu'à un certain point.** Une
  fenêtre glissante avec un buffer de d rondes coûte au plus 2,3 % d'erreurs
  logiques. Un seul cœur suit des rondes de 1 µs jusqu'à d = 9 à p = 0,1 % et
  jusqu'à d = 5 à p = 0,3 %. Au-delà, le retard s'accumule, et la figure
  ci-dessous le montre.
- **Même réponse qu'une référence lente et évidemment correcte.** Le décodeur
  rapide rend exactement la même correction qu'une implémentation de
  référence sur 75 000 cas aléatoires, et le test par mutation montre que les
  tests attrapent 16 bogues injectés sur 16. Le fonctionnement est expliqué
  dans [docs/decoder.md](docs/decoder.md) (en anglais).

![Taux d'erreur logique d'Union-Find relatif à PyMatching](results/accuracy_vs_pymatching.png)

### Flux continu : le backlog, mesuré

![Décodage en fenêtres glissantes : ce que la fenêtre coûte, et si un cœur suit des rondes de 1 µs](results/streaming.png)

Un décodeur temps réel ne peut pas attendre la fin de l'expérience.
`SlidingWindowDecoder` décode `commit + buffer` rondes à la fois, garde la
correction des `commit` rondes les plus vieilles, reporte son effet vers
l'avant et glisse ([fonctionnement](docs/decoder.md#7-sliding-windows-decoding-as-the-rounds-arrive)).

- **À gauche : un buffer de d rondes suffit.** Sur les mêmes tirages, il
  donne 0,7 à 2,3 % plus d'erreurs logiques que le décodage de l'expérience
  complète d'un coup (d = 3 à 9, p = 0,5 %, 10 d rondes). Sans buffer : 1,4 à
  2,1 fois plus.
- **Au milieu et à droite : un cœur suit-il ?** Les rondes arrivent à chaque
  microseconde, et un seul cœur décode les fenêtres dans l'ordre, chacune
  mesurée en C++. Les traits pleins restent bornés : le décodeur suit. Les
  pointillés croissent sans limite : c'est le backlog.

| p | suit jusqu'à | travail par ronde commitée | premier à décrocher |
|---|---|---|---|
| 0,1 % | d = 9 | 0,73 µs | d = 11 (1,20 µs) |
| 0,3 % | d = 5 | 0,74 µs | d = 7 (1,64 µs) |

Le flux continu coûte environ deux fois le travail du décodage entier par
ronde, puisque chaque ronde est décodée une fois dans un buffer et une fois
dans une région de commit. Suivre en moyenne ne dit pas tout non plus : à
d = 9 et p = 0,1 %, le cœur est occupé 73 % du temps, et une rafale de
fenêtres difficiles a poussé un délai jusqu'à 1,3 ms. Aller plus loin demande
des fenêtres en parallèle sur plusieurs cœurs, ou du matériel.

### Débit sur des expériences complètes

![Temps de décodage par ronde de syndromes, UF backlog contre PyMatching](results/latency.png)

Un seul fil, Intel Xeon à 2,1 GHz (VM infonuagique à 2 cœurs), 20 000 tirages
par point, expériences de mémoire complètes de d rondes :

| d | p | UF p50 | UF p99 | UF moyenne | PyMatching moyenne |
|---|---|---|---|---|---|
| 5 | 0,1 % | 0,32 µs | 3,1 µs | 0,46 µs | 0,49 µs |
| 9 | 0,1 % | 2,3 µs | 11,4 µs | 2,9 µs | 3,5 µs |
| 13 | 0,1 % | 8,2 µs | 26,1 µs | 9,5 µs | 11,0 µs |
| 5 | 0,3 % | 1,1 µs | 8,1 µs | 1,6 µs | 1,6 µs |
| 9 | 0,3 % | 11,3 µs | 41,9 µs | 11,8 µs | 12,2 µs |
| 13 | 0,3 % | 38,4 µs | 97,6 µs | 40,0 µs | 40,7 µs |

### Matériel (v0 : 1D, non pondéré)

Cœur Union-Find en VHDL, identique bit pour bit à son modèle de référence sur
plus de 6 000 vecteurs (N = 8, 16, 32). Après placement-routage sur un iCE40
HX8K : 94,5 MHz à N = 8, soit environ 10,6 ns par ronde de croissance. Le Fmax
baisse avec N et la cause est identifiée : des chaînes de XOR en cascade,
qu'un scan préfixe parallèle peut corriger. [Détails](hardware/README.md).

## Utilisation

```bash
pip install .     # compile le cœur C++ : requiert un compilateur C++17 et CMake
```

```python
import stim
import backlog

p = 0.003
circuit = stim.Circuit.generated(
    "surface_code:rotated_memory_z", distance=5, rounds=50,
    after_clifford_depolarization=p, before_round_data_depolarization=p,
    before_measure_flip_probability=p, after_reset_flip_probability=p)
dets, obs = circuit.compile_detector_sampler().sample(20_000, separate_observables=True)

decoder = backlog.UnionFindDecoder.from_circuit(circuit)  # pondéré par défaut
predictions = decoder.decode_batch(dets)
print("taux d'erreur logique :", (predictions != obs).any(axis=1).mean())

# Flux continu : 5 rondes commitées par fenêtre, 5 rondes de buffer
window = backlog.SlidingWindowDecoder.from_circuit(circuit, commit=5, buffer=5)
print("en fenêtres :", (window.decode_batch(dets) != obs).any(axis=1).mean())
print(window.windows[:3])  # (première ronde, fin du commit, fin de la fenêtre)
```

Comme décodeurs sinter, en Python :

```python
stats = sinter.collect(
    num_workers=4, tasks=tasks, decoders=["backlog-uf", "uf-window"], max_shots=10**6,
    custom_decoders={**backlog.sinter_decoders(),
                     "uf-window": backlog.SinterSlidingWindowDecoder(commit=5, buffer=5)})
```

ou en ligne de commande :

```bash
sinter collect --circuits circuit.stim --decoders backlog-uf pymatching \
    --custom_decoders_module_function "backlog:sinter_decoders" \
    --max_shots 100000 --processes 4 --save_resume_filepath stats.csv
```

`backlog-uf-unweighted` est aussi disponible, pour comparer.

## Reproduire

```bash
pip install -r requirements.txt && make install   # dans un virtualenv
make test        # tests C++ et fuzzing, pytest, vérifications en ligne de commande
make mutants     # test par mutation, environ deux minutes
make compare     # balayage PyMatching contre Union-Find avec sinter (long, reprenable)
make streaming   # fenêtres glissantes : précision, latence, backlog (quelques minutes)
make latency     # temps de décodage des expériences complètes, sur une machine au repos
make hw-sim      # simulation VHDL bit pour bit (requiert ghdl)
make hw          # synthèse VHDL, placement-routage (requiert yosys, nextpnr-ice40)
make help        # tout le reste
```

## Comment la justesse est vérifiée

1. **Chaque correction est valide.** Elle doit reproduire le syndrome
   observé. C'est vérifié à chaque décodage de chaque test, et le décodeur en
   ligne de commande retourne un code d'erreur s'il échoue une seule fois.
2. **Le rapide égale la référence.** `ReferenceDecoder` est une version
   courte, synchrone et évidemment correcte de l'algorithme. Le décodeur
   rapide, piloté par événements, doit rendre la même correction, arête pour
   arête : 75 000 cas aléatoires (arêtes parallèles comprises), plus de vrais
   modèles d'erreurs de surface code.
3. **Les fenêtres sont vérifiées de la même façon.** Sur 75 000 graphes en
   couches générés aléatoirement, avec des tailles de fenêtre aléatoires, les
   corrections commitées reproduisent toujours le syndrome, et une seule
   fenêtre qui couvre toutes les rondes donne exactement la réponse du
   décodage entier. Un test unitaire fixe le cas des erreurs coupées par le
   bord de la fenêtre.
4. **Les tests attrapent les bogues.** `make mutants` injecte 16 bogues
   réalistes, un à la fois (mauvais taux de croissance, rayon perdu à la
   fusion, forêt qui ignore les poids, inversions de syndrome pas reportées,
   fenêtres qui ne se chevauchent pas...) et exige que la suite échoue à
   chaque fois.
5. **La précision a un seuil d'échec.** pytest échoue si l'Union-Find pondéré
   dépasse 1,35 fois le taux d'erreur logique de PyMatching à d = 5,
   p = 0,5 %, ou si un buffer de d rondes coûte plus de 5 % par rapport au
   décodage entier ; les vérifications en ligne de commande échouent au-delà
   de 1,5 fois PyMatching sur quatre codes.
6. **Matériel.** Le testbench VHDL compare chaque sortie au modèle de
   référence bit pour bit, et vérifie l'invariant du syndrome de façon
   indépendante.

Tout ça roule dans la CI à chaque push.

## Organisation

```
src/backlog/   paquet Python : conversion du modèle d'erreurs, décodeurs, plugin sinter
cpp/           cœur C++17 (aucune dépendance) : décodeurs rapide, de référence et en
               fenêtres glissantes, tests, CLI, liaisons
tests/         pytest : seuils de précision, fenêtres, intégration sinter
python/        expériences : circuits, balayages, bancs d'essai, figures
hardware/      cœur VHDL 1D : modèle de référence, RTL, testbench, synthèse, placement-routage
scripts/       vérifications en ligne de commande, test par mutation
results/       données et figures produites par les scripts
docs/          fonctionnement du décodeur, feuille de route
```

## Limites

- **Un cœur, fenêtres dans l'ordre.** Les chiffres de flux continu viennent
  de fenêtres décodées l'une après l'autre sur un seul cœur. La simulation du
  backlog enchaîne des fenêtres mesurées sur des tirages indépendants, une
  approximation d'une longue expérience. Les fenêtres en parallèle sont la
  prochaine étape.
- **Égalité de vitesse, pas une victoire.** À p = 0,3 %, ce décodeur ne fait
  qu'égaler PyMatching 2 sur un cœur.
- **Précision.** L'Union-Find pondéré fait encore 5 à 20 % plus d'erreurs
  logiques que le matching à d = 7 et 9.
- **Machine partagée.** Les temps viennent d'une VM infonuagique à 2 cœurs.
  Les reprises concordent à environ 5 % près, mais les temps absolus seront
  différents sur votre machine.
- **Portée.** Modèles d'erreurs en graphe seulement (Stim décompose les
  hyperarêtes), jusqu'à 64 observables logiques, expériences de mémoire (pas
  encore de lattice surgery).
- **Le matériel en est à la v0.** Le cœur VHDL est en 1D, non pondéré, en
  capacité de code. Ses chiffres ne se comparent pas aux décodeurs 2D de
  surface code comme Helios, et il n'a pas encore roulé sur une carte : le
  Fmax vient du placement-routage.

## Feuille de route

Fait : baseline Stim + PyMatching, Union-Find pondéré piloté par événements
avec intégration sinter, décodage en fenêtres glissantes avec le backlog
mesuré, cœur VHDL 1D. À venir : fenêtres en parallèle sur plusieurs cœurs,
scan préfixe parallèle dans le cœur VHDL, puis un cœur 2D de surface code.
Détails dans [docs/roadmap.md](docs/roadmap.md).

Les références et la licence (MIT) sont dans la [version anglaise](README.md).
