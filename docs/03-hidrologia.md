# Hidrologia

## Umbral de area drenada

Define desde que superficie de captacion se considera que existe cauce. Se
expresa en **hectareas y no en celdas** para que el valor calibrado siga siendo
valido al cambiar de modelo de elevacion: 250 celdas son 22.5 ha sobre un DEM
de 30 m y 1 ha sobre uno remuestreado a 2 m.

**Valor calibrado:** 22.5 ha, contrastado contra la quebrada Shicopreto en la
concesion Cocama, Madre de Dios. La red generada reproduce el 86.8% del cauce
levantado en campo a 60 m y el 97.2% a 100 m. Bajar el umbral a 2 ha solo
agrega 4 puntos de cobertura y triplica la longitud de red.

## Ordenes de flujo

**Strahler** es un orden topologico: solo sube cuando confluyen dos cauces del
mismo orden, por lo que satura. Es el que reconocen la normativa y los
evaluadores.

**Shreve** suma las magnitudes de los tramos que confluyen, de modo que
correlaciona mejor con el caudal real. Es el indicado cuando el criterio de
faja es hidrologico y no administrativo.

Ambos se calculan siempre y quedan como campo; el criterio elegido decide cual
manda la tabla de anchos.

## Fajas marginales

La tabla orden a ancho es **un punto de partida, no una constante normativa**:
la autoridad las fija caso por caso. El informe declara la tabla aplicada.

Las clases se resuelven de mayor a menor ancho y cada una se recorta contra las
anteriores, de modo que **no se solapan**: donde concurren dos fajas rige la
mas exigente, y la suma de areas es la real.

## Diagnostico topologico

Antes de calcular ordenes se mide la calidad del cosido de la red. Si los
tramos no se encuentran en las confluencias, el orden nunca sube y toda la red
queda en orden 1 sin sintoma visible. El numero a mirar es **confluencias
frente a tramos**: por debajo del 5% la red esta despegada y hay que subir la
tolerancia de nodo.
