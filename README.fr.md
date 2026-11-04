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
décodeur pour ce problème et le mesure honnêtement à chaque étape.

## Résultats

Mémoire sur surface code rotatif, bruit au niveau circuit généré par Stim,
rondes = d. Tous les décodeurs passent par sinter sur les mêmes circuits :
12,8 millions de tirages, 96 points, au moins 68 erreurs logiques par point.
Les barres d'erreur valent un écart-type. Pour reproduire : `make compare` et
`make latency`.

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
- **Vitesse : à égalité avec PyMatching 2, pas une raclée.** Sparse Blossom
  est très rapide. Sur un cœur, ce décodeur est 15 à 19 % plus rapide à
  p = 0,1 % à partir de d = 9, à égalité à p = 0,3 %, et plus lent à d = 3
  (les reprises concordent à environ 5 % près). Le vrai avantage
  d'Union-Find est matériel : ses règles locales se traduisent en logique
  parallèle.
- **Même réponse qu'une référence lente et évidemment correcte.** Le décodeur
  rapide rend exactement la même correction que l'implémentation de référence
  sur 75 000 cas générés aléatoirement, et le test par mutation montre que les
  tests attrapent 10 bogues injectés sur 10. Le fonctionnement est expliqué
  dans [docs/decoder.md](docs/decoder.md) (en anglais).

![Taux d'erreur logique d'Union-Find relatif à PyMatching](results/accuracy_vs_pymatching.png)

### Temps de décodage

![Temps de décodage par ronde de syndromes, UF backlog contre PyMatching](results/latency.png)

Un seul fil, Intel Xeon à 2,1 GHz (VM infonuagique à 2 cœurs), 20 000 tirages
par point. Les temps sont par tirage, qui contient d rondes de syndromes :

| d | p | UF p50 | UF p99 | UF moyenne | PyMatching moyenne |
|---|---|---|---|---|---|
| 5 | 0,1 % | 0,32 µs | 3,1 µs | 0,46 µs | 0,49 µs |
| 9 | 0,1 % | 2,3 µs | 11,4 µs | 2,9 µs | 3,5 µs |
| 13 | 0,1 % | 8,2 µs | 26,1 µs | 9,5 µs | 11,0 µs |
| 5 | 0,3 % | 1,1 µs | 8,1 µs | 1,6 µs | 1,6 µs |
| 9 | 0,3 % | 11,3 µs | 41,9 µs | 11,8 µs | 12,2 µs |
| 13 | 0,3 % | 38,4 µs | 97,6 µs | 40,0 µs | 40,7 µs |

Divisez par d pour comparer à un cycle de 1 µs. À p = 0,1 %, la moyenne suit
jusqu'à d = 13, mais la queue p99 décroche à partir de d = 9. À p = 0,3 %, la
moyenne décroche à partir de d = 9. Un seul cœur de CPU ne tient pas le temps
réel à grande échelle, peu importe lequel des deux décodeurs y roule : c'est
le problème du backlog, et c'est pourquoi les prochaines étapes sont le
décodage en fenêtres glissantes et le matériel.

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
    "surface_code:rotated_memory_z", distance=5, rounds=5,
    after_clifford_depolarization=p, before_round_data_depolarization=p,
    before_measure_flip_probability=p, after_reset_flip_probability=p)
decoder = backlog.UnionFindDecoder.from_circuit(circuit)  # pondéré par défaut

dets, obs = circuit.compile_detector_sampler().sample(100_000, separate_observables=True)
predictions = decoder.decode_batch(dets)
print("taux d'erreur logique :", (predictions != obs).any(axis=1).mean())

result = decoder.decode_detailed(dets[0])  # arêtes choisies, vérification du syndrome
```

Comme décodeur sinter, en Python :

```python
stats = sinter.collect(num_workers=4, tasks=tasks, decoders=["backlog-uf"],
                       custom_decoders=backlog.sinter_decoders(), max_shots=10**6)
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
make mutants     # test par mutation, environ une minute
make compare     # balayage PyMatching contre Union-Find avec sinter (long, reprenable)
make latency     # banc d'essai du temps de décodage, sur une machine au repos
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
   arête : 75 000 cas aléatoires sur des graphes générés, plus de vrais
   modèles d'erreurs de surface code.
3. **Les tests attrapent les bogues.** `make mutants` injecte 10 bogues
   réalistes, un à la fois (mauvais taux de croissance, rayon perdu à la
   fusion, forêt qui ignore les poids, événement décalé d'une ronde...) et
   exige que la suite échoue à chaque fois.
4. **La précision a un seuil d'échec.** pytest échoue si l'Union-Find pondéré
   dépasse 1,35 fois le taux d'erreur logique de PyMatching à d = 5,
   p = 0,5 % (20 000 tirages) ; les vérifications en ligne de commande
   échouent au-delà de 1,5 fois sur le code de répétition et sur le surface
   code à d = 3, 5, 7.
5. **Matériel.** Le testbench VHDL compare chaque sortie au modèle de
   référence bit pour bit, et vérifie l'invariant du syndrome de façon
   indépendante.

Tout ça roule dans la CI à chaque push.

## Organisation

```
src/backlog/   paquet Python : conversion du modèle d'erreurs, API, plugin sinter
cpp/           cœur C++17 (aucune dépendance), décodeur de référence, tests, CLI, liaisons
tests/         pytest : seuils de précision, intégration sinter, rapide égale référence
python/        expériences : circuits, balayages, bancs d'essai, figures
hardware/      cœur VHDL 1D : modèle de référence, RTL, testbench, synthèse, placement-routage
scripts/       vérifications en ligne de commande, test par mutation
results/       données et figures produites par les scripts
docs/          fonctionnement du décodeur, feuille de route
```

## Limites

- **Débit, pas latence en flux continu.** Tous les chiffres ci-dessus
  décodent une expérience de mémoire complète d'un coup. Un décodeur temps
  réel travaille sur une fenêtre glissante de rondes, à mesure qu'elles
  arrivent. C'est la prochaine étape.
- **Égalité de vitesse, pas une victoire.** À p = 0,3 %, ce décodeur ne fait
  qu'égaler PyMatching 2 sur un cœur.
- **Précision.** L'Union-Find pondéré fait encore 5 à 20 % plus d'erreurs
  logiques que le matching à d = 7 et 9.
- **Machine partagée.** Les temps viennent d'une VM infonuagique à 2 cœurs.
  Les reprises concordent à environ 5 % près, mais les temps absolus seront
  différents sur votre machine.
- **Portée.** Modèles d'erreurs en graphe seulement (Stim décompose les
  hyperarêtes), jusqu'à 64 observables logiques.
- **Le matériel en est à la v0.** Le cœur VHDL est en 1D, non pondéré, en
  capacité de code. Ses chiffres ne se comparent pas aux décodeurs 2D de
  surface code comme Helios, et il n'a pas encore roulé sur une carte : le
  Fmax vient du placement-routage.

## Feuille de route

Fait : baseline Stim + PyMatching, Union-Find pondéré piloté par événements
avec intégration sinter, cœur VHDL 1D. À venir : décodage en fenêtres
glissantes avec latence de queue par ronde, scan préfixe parallèle dans le
cœur VHDL, puis un cœur 2D de surface code. Détails dans
[docs/roadmap.md](docs/roadmap.md).

Les références et la licence (MIT) sont dans la [version anglaise](README.md).
