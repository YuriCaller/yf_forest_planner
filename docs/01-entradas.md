# Entradas

## Capas requeridas

**Censo forestal** — capa de puntos con los individuos. Puede usarse solo la
seleccion, util para correr una parcela de corta sin partir la capa.

**Poligono de PCA o concesion** — define el area de trabajo. Recorta la red
hidrica, acota la superficie de costo y es la base de todos los porcentajes de
impacto.

**Modelo de elevacion** — si esta en coordenadas geograficas se reproyecta
automaticamente al sistema de trabajo, con remuestreo bilineal y a resolucion
nativa.

## Sistema de referencia

Debe ser **proyectado en metros**. El complemento propone la zona UTM que
corresponde a la extension de los datos y bloquea la ejecucion si el sistema es
geografico: con grados de por medio, un patio de 25 por 40 m se calcularia como
25 por 40 grados y el resultado no falla, solo miente.

Si los datos cruzan dos zonas UTM se advierte pero no se impide: trabajar toda
una concesion en una sola zona es lo correcto aunque una esquina caiga fuera.

## Panel de estado

Resume si las entradas permiten ejecutar, con una marca por cada requisito.
Conviene revisarlo antes de correr y no despues: una corrida completa puede
tardar varios minutos.

## Un aviso sobre el remuestreo

Remuestrear un DEM de 30 m a 2 m no agrega informacion topografica. La
resolucion real sigue siendo 30 m y lo que se obtiene son 225 celdas donde
habia una, todas interpoladas del mismo dato. Para hidrologia perjudica: se
subestima la pendiente y el remuestreo cubico introduce depresiones
artificiales.

Para cartografia si sirve, pero hagalo en la simbologia de la capa y no
escribiendo un raster nuevo: asi el archivo en disco sigue intacto.
