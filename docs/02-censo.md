# Censo

## Mapeo de campos

El complemento detecta los campos por su nombre, tolerando mayusculas, tildes y
separadores. Los alias reconocen las formas habituales de los censos peruanos:
`TIPO` para categoria, `VOL3` para volumen, `BQ` y `FAJA` para parcela.

**No filtre los combos por campos numericos.** Es frecuente que un error de
hoja de calculo contamine la columna de volumen y el shapefile la tipe como
texto; el complemento la lee igual y lo advierte.

## Volumen

Si el censo trae volumen, se usa. Si no, se calcula:

    V = (pi / 4) x DAP^2 x Hc x ff

El **modo de diametro** se infiere por magnitud y no solo por el nombre del
campo: un valor de 0.75 no es ambiguo, porque no existe un arbol censable de
0.75 cm. En censos amazonicos peruanos el DAP suele venir en metros.

El **factor de forma** de 0.65 es de uso corriente en Amazonia peruana, pero
verifique que corresponda al aprobado por la autoridad.

## Verificaciones automaticas

- Rango fisico de DAP y altura comercial
- Coherencia del factor de forma: si el censo trae volumen y dimensiones, el
  factor implicito debe ser constante; una dispersion delata celdas con la
  formula sobrescrita
- Grafias distintas de la misma especie, que de otro modo generarian dos
  entradas en cualquier salida por especie

## Diametro minimo de corta

Un individuo por debajo del DMC de su especie **no puede talarse**, asi que no
suma volumen al plan ni se asigna a un patio.

El boton de DMC oficial carga la tabla de la Resolucion Jefatural
458-2002-INRENA, vigente a nivel nacional. Tres advertencias:

1. Es el **minimo legal, no el sostenible**. Una evaluacion del modelo de
   concesiones concluyo que esos DMC no contribuyen a la sostenibilidad y que
   cada titular debe determinar y justificar los suyos. Concesiones
   certificadas cortan shihuahuaco a 70 cm cuando el legal es 51.
2. La correspondencia es por **nombre comun**, que varia entre regiones.
   Verifiquela contra el nombre cientifico.
3. Las especies que no figuran **no se verifican**. El complemento las lista
   para que complete la tabla; inventarles un umbral seria peor.

La tabla se importa y exporta en CSV, de modo que se prepara una vez y se
reutiliza.

## CITES

El shihuahuaco y el tahuari estan en el Apendice II de la CITES desde la COP19
de noviembre de 2022. El complemento lo senala como aviso: la gestion de
permisos y del dictamen de extraccion no perjudicial va por fuera del plan.
