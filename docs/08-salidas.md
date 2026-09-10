# Salidas

## Capas

Se cargan simbolizadas. La red hidrica con grosor graduado por orden, las fajas
en relleno translucido, el arrastre con flechas convergentes, los patios
graduados por volumen y las obras de cruce con simbolo por tipo.

El censo evaluado usa **forma ademas de color** —visto, cruz y rombo— porque en
impresion a escala de grises el color desaparece y la forma no.

Los rasters de densidad e intensidad se clasifican **contra el limite
normativo** cuando existe, de modo que el mapa muestra directamente que
hectareas se exceden.

## GeoPackage

Opcional. Sin ruta, las capas quedan temporales y se pierden al cerrar QGIS.

## Informe

Se genera en HTML y Word, se escribe en una carpeta temporal y se abre solo.
Guardelo desde ahi donde lo necesite.

Incluye la **trazabilidad de insumos**: cada capa con su sistema de referencia,
numero de entidades y resumen criptografico SHA-256 cuando existe en disco. Las
capas en memoria se declaran como no verificables en lugar de inventarles un
identificador.

Y separa **estimacion de control**: la geometria de vias, patios y pistas es
una propuesta sujeta a replanteo; los volumenes y las exclusiones por DMC,
especie y faja son verificaciones contra el censo.

## Cuadros en Excel

Diez hojas: resumen, por especie, distribucion diametrica, semilleros, no
aprovechables con su motivo, por patio, areas de impacto, balance de volumen,
intensidad y padron de arboles.

**El formato exigible lo fija la autoridad.** En Peru rige la RDE
046-2016-SERFOR-DE; contraste estos cuadros con el formato vigente de su
jurisdiccion antes de presentarlos.

## Semilla aleatoria

Fija el agrupamiento de patios. Sin ella, cada corrida daria un resultado
distinto y el resumen criptografico del informe dejaria de garantizar nada.
