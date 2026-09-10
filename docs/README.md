# Forest Planner — Manual

Complemento de QGIS que va del censo forestal al plan de manejo: red hidrica y
fajas marginales, patios y centros de acopio, vias de saca, areas de impacto y
estimacion de viajes de transporte.

**Los trazos, ubicaciones y cantidades que genera son propuestas tecnicas
sujetas a verificacion y replanteo en campo. No constituyen un diseno
definitivo.**

## Como usarlo

El complemento se organiza en modulos que se ejecutan en cadena, cada uno
consumiendo lo que produjo el anterior. El orden importa.

| Modulo | Que hace | Necesita |
|---|---|---|
| M1 | Red hidrica y fajas marginales | DEM |
| M2 | Humedad, aguajales y zonas inundables | DEM, M1 |
| M3 | Patios y centros de acopio | Censo, M1 |
| M4 | Superficie de costo | DEM, M1 |
| M5 | Red de vias | M3, M4, punto de salida |
| M6 | Areas de impacto | M1, M3, M5 |
| M7 | Densidad e intensidad | Censo |
| M8 | Rendimiento y viajes | Censo, M3 |

## Secciones

1. [Entradas](01-entradas.md) — capas, sistema de referencia y estado
2. [Censo](02-censo.md) — mapeo de campos, volumen y diametro minimo de corta
3. [Hidrologia](03-hidrologia.md) — cauces, ordenes y fajas marginales
4. [Patios](04-patios.md) — patios, centros de acopio y escenarios
5. [Vias](05-vias.md) — superficie de costo, rasante y trazado
6. [Normativa](06-normativa.md) — limites por jurisdiccion e impacto
7. [Modulos](07-modulos.md) — que ejecutar y en que orden
8. [Salidas](08-salidas.md) — capas, informe y cuadros

## Antes de empezar

**El sistema de referencia debe ser proyectado en metros.** Todo el analisis
son distancias y areas: con un sistema geografico los resultados salen
plausibles y estan mal. El complemento lo bloquea.

**El modelo de elevacion decide la calidad de todo lo demas.** Copernicus
GLO-30 y SRTM son modelos de superficie: sobre bosque cerrado miden la copa y
no el suelo, con un sesgo de 10 a 25 m. ANADEM parte de Copernicus y remueve
la vegetacion con un algoritmo calibrado para Sudamerica; el error medio baja
de 9.6 a 1.5 m. Para Amazonia, ANADEM es la mejor opcion libre.
