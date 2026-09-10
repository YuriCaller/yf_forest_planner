# Modulos

## Orden de ejecucion

Cada modulo consume lo que produjo el anterior. Ejecutarlos fuera de orden no
falla, pero produce resultados incompletos.

    M1 hidrologia
     |
     +--> M2 humedad          (usa el DEM y la red)
     +--> M3 patios           (usa las fajas como exclusion)
     +--> M4 costo            (usa fajas y cauces)
              |
              +--> M5 vias    (usa el costo, M3 y el punto de salida)
                       |
                       +--> M6 impacto   (consolida M1, M3 y M5)

    M7 intensidad y M8 transporte solo necesitan el censo.

## Que ejecutar

**Primera vez sobre un predio nuevo:** M1 solo. Verifique que la red hidrica se
parezca a lo que conoce del terreno antes de construir nada encima.

**Prueba rapida de vias:** M1, M3, M4 y M5 con el maximo de destinos en 5. Cada
destino es un calculo sobre el grafo completo; con noventa patios el trazado
tarda varios minutos.

**Corrida completa:** todos, y despues los documentos desde la pestana Salidas.

## Que mirar en el registro

- **M1** — confluencias frente a tramos: por debajo del 5% la red esta despegada
- **M4** — variacion relativa: por debajo de 0.15 la superficie no guia el
  trazado, y el porcentaje de celdas con impedimento
- **M5** — densidad en m/ha: fuera del rango de 13 a 16 conviene revisar
- **M7** — celdas sobre el limite: es el numero que revisa un evaluador
