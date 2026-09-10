# Vias

## Punto de salida

La red converge hacia el: empalme con via existente, puerto o campamento. Es
una decision de acceso real que ningun modelo puede deducir del terreno, y sin
ella el modulo no puede ejecutarse.

Tres formas de definirlo: marcarlo en el mapa, escribir sus coordenadas o
tomarlo de una capa de puntos.

**Si declara vias existentes que ya salgan del area, el punto de salida deja de
ser obligatorio.**

## Vias existentes

Si la PCA ya esta cruzada por un vial —tipicamente el de la parcela anterior—,
declarelo y la red convergira hacia el en lugar de abrir traza paralela.

El costo relativo controla si se usa tal cual o exige rehabilitacion: de 0.0005
a 0.01 para una via en buen estado, de 0.05 a 0.30 para una trocha. Su longitud
se reporta aparte porque **no es apertura nueva y no suma al impacto**.

## Dos pendientes distintas

Es la distincion que gobierna el trazado y conviene tenerla clara.

**Pendiente transversal del terreno** — determina el movimiento de tierras, que
puede llegar al 74% del costo de construccion. Es un costo creciente, no un
limite.

**Rasante de la via** — la pendiente en el sentido de avance. Es la que limita
si el camion sube cargado, y es un limite duro.

Son independientes: una via puede cruzar una ladera al 40% con rasante del 5%
siguiendo la curva de nivel, pagando mucha obra de tierra.

Como el trazado corre sobre un grafo dirigido, **subir y bajar tienen limites
distintos**: los estandares de via primaria admiten del orden del 8% bajando
cargado y la mitad subiendo.

## Orden de vecindario

Decide que rasantes son **representables**. En una malla, las pendientes
alcanzables estan cuantizadas por los movimientos permitidos. Sobre una ladera
al 30%:

| Orden | Vecinos | Rasante minima no nula |
|---|---|---|
| 1 | 8 | 21.2% |
| 2 | 16 | 13.4% |
| 3 | 32 | 9.5% |
| 4 | 48 | 7.3% |

Con orden 1 la via baja mas empinada de lo admisible por pura geometria de la
malla, no por costo. En terreno llano basta 1; **en colinas hace falta 3 o 4**
para que la via pueda desarrollarse, a costa de memoria.

## Cruces de cauce

El costo de cruce es **finito y escalado por orden**: 20 para una alcantarilla,
800 para un puente. Con costo infinito el algoritmo no distinguiria cruzar de
recorrer en paralelo y la red quedaria fragmentada por el propio drenaje.

La geometria hace el resto: cruzar en perpendicular son dos celdas, recorrer en
paralelo son veinte. Es el criterio de la ingenieria vial forestal, donde el
angulo de cruce es la restriccion de diseno para proteger el curso de agua.

## Cobertura por corredor

Un patio a menos de la distancia de servicio de una via ya trazada **no recibe
ramal propio**: se alcanza por pista de arrastre. Asi la red crece por
corredores que sirven grupos, como en las redes reales, en lugar de un ramal
por patio.

Sin esto, la densidad se dispara: sobre el Cocama, 24.8 m/ha con un ramal por
patio frente a 15.5 m/ha con cobertura por corredor.

**Referencia de densidad:** 13.2 y 15.3 m/ha en las PCA 07 y 08 de la concesion
Paujil, Madre de Dios; 16 m/ha como optimo economico segun Braz (1997).

## Salir del area

No esta prohibido. Cuando un aguajal o una quebrada encajonada bloquean el paso
interno, rodear por fuera puede ser mas barato y de menor impacto. Se penaliza
para que no sea la opcion por defecto, y los kilometros que quedan afuera se
reportan aparte: verifique el derecho de paso y declarelos, porque no computan
en el impacto del predio.

## Afinado de la traza

La ruta sobre malla produce diente de sierra cuando la direccion ideal no
coincide con ningun vecino. El afinado quita los picos, simplifica y redondea,
sin desplazar el eje fuera del corredor calculado.

Medido sobre una red real: los giros mayores a 60 grados bajaron del 42% al
0.4% y la longitud cayo casi 8 km, que eran zigzag artificial.
