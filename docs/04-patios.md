# Patios y centros de acopio

## Dos niveles, no uno

**Patio** recibe arrastre. El fuste llega a traccion sobre pista, sin via
construida. Es efimero y su ubicacion la manda la distancia maxima de arrastre.

**Centro de acopio** recibe transporte. De ahi sale en camion o en balsa, asi
que exige via con capacidad de carga, superficie de maniobra y acceso todo el
ano. Ahi se cubica, se marca y se emite la guia.

El flujo es arbol, patio, centro, salida.

## Parametros calibrados

Los valores por defecto se contrastaron con literatura amazonica:

| Parametro | Valor | Fuente |
|---|---|---|
| Distancia maxima de arrastre | 400 m | Silva et al. (2018), Acre |
| Volumen maximo por patio | 350 m3 | Silva et al. (2018) |
| Area de patio | 500 m2 | Silva et al. (2018), 20 x 25 m |
| Pendiente maxima del sitio | 15% | Silva et al. (2018) |

**Contraste independiente:** con esos valores, el modelo produce sobre el censo
del Cocama un arrastre medio de 162 m, frente a los 160 m que obtuvo Braz
(1997) para 20 m3/ha en terreno plano; y 18.7 m2 de patio por hectarea, frente
a los 24 que midieron Johns et al. (1996) en Paragominas.

## Volumen minimo y fusion

Un patio de 500 m2 habilitado para 15 m3 no lo abre nadie. Por debajo del
volumen minimo, el patio se disuelve y sus arboles pasan al vecino, aceptando
mas arrastre a cambio de menos infraestructura.

La fusion solo procede si **todos** los arboles tienen otro patio dentro del
radio de arrastre: perder volumen aprovechable es peor que habilitar un patio
chico.

## Que restriccion manda

En llanura amazonica manda casi siempre la **distancia**, no el volumen. Sobre
el Cocama, el uso medio de capacidad es del 31%: se necesitan 90 patios para
cubrir el area aunque por volumen bastarian 32.

## Escenarios de centros

El modulo evalua varios numeros de centros y reporta el **momento de
transporte**, que es la suma del volumen por la distancia patio-centro.

**No devuelve un ganador.** La eleccion depende del costo de habilitar un
centro frente al ahorro de saca, y ese costo lo conoce el formulador. El
modulo entrega las magnitudes para que la decision quede sustentada.

Si un escenario con mas centros reduce mucho los cruces de cauce mayor, la
topografia esta partiendo el area y ese centro no compite con el primero: lo
exige el terreno.

## Alcance

Las distancias de este modulo son **euclidianas**. La distancia real por via la
resuelve el modulo de caminos y puede ser bastante mayor donde el relieve o los
humedales obligan a rodear.
