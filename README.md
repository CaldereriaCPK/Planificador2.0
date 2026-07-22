# Planificador de proyectos

Aplicación web desarrollada con Flask para planificar proyectos de fabricación. Unifica calendario, control de fases y seguimiento de pedidos en un único entorno donde se recalculan horas, se reciben tarjetas de Kanbanize y se guardan todas las observaciones en disco.

## Puesta en marcha rápida

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt  # o al menos Flask
python app.py
```

La aplicación escucha por defecto en `http://localhost:9000`. La primera pantalla que se abre es la vista **Completo**, que combina el calendario principal y la tabla de proyectos.

### Datos persistentes

* Los proyectos se guardan en `data/projects.json`. El fichero se vuelve a leer cada vez que se carga una vista, así que cualquier edición manual también se refleja automáticamente.
* Define la variable de entorno `EFIMERO_DATA_DIR` para mover todos los ficheros de datos (`projects.json`, notas, adjuntos, etc.) a otra ruta.
* Las imágenes que se adjuntan a los proyectos se almacenan en `static/uploads` y se eliminan cuando se sustituyen o cuando el proyecto deja de necesitarlas.

### Integración con Kanbanize

La aplicación recibe tarjetas a través del webhook de Kanbanize. Solo se mantienen las tarjetas de la lane «Seguimiento Compras». Las tarjetas sin fecha límite confirmada aparecen en la columna lateral **Sin fecha de entrega confirmada**. Las tarjetas que pasan a «Ready to Archive» provocan el archivado automático del proyecto y de sus fases.

## Vistas principales

### Completo

La vista **Completo** agrupa todos los componentes principales:

* Calendario con desplazamiento horizontal usando `Shift + rueda del ratón`. El botón **HOY** centra la vista. El calendario precarga desde tres meses antes hasta seis meses después de la fecha actual y recuerda la posición tras recargar.
* Lista de conflictos siempre visible. Al pulsar sobre un conflicto se resaltan automáticamente las fases afectadas.
* Tabla de proyectos con filtros por nombre y cliente, edición directa de fechas de inicio, asignación de trabajadores y columna de estado planificado (✔/❌). Cada fila incluye un botón rojo **X** para eliminar el proyecto.
* Columna lateral **Fases sin planificar** redimensionable (ancho inicial 2500 px) con plegado por proyecto y orden por fecha de material confirmado. Las carpetas muestran la fecha límite y se resaltan al interactuar con el calendario.

### Calendario de pedidos

El calendario de pedidos muestra las tarjetas agrupadas por semana y permite reorganizarlas por `drag & drop`. Las principales características son:

* Todas las celdas de una fila comparten la altura de la celda con más pedidos. El ajuste se recalcula automáticamente al filtrar, al añadir o eliminar pedidos, tras recibir actualizaciones en directo y cuando cambia el ancho de la ventana.
* Formulario rápido para crear tarjetas puntuales visibles en la semana seleccionada.
* Campo de filtro que busca por proyecto, cliente, código y texto libre. El filtrado atenúa tanto las tarjetas como las filas asociadas en la tabla lateral.
* Al hacer clic en un pedido se resalta el resto de tarjetas con el mismo título y la fila correspondiente en la tabla.

#### Columna lateral «Columna 1»

Junto al calendario aparece la tabla **Columna 1** con la información de cada proyecto relevante para pedidos:

* Las columnas se pueden ordenar y redimensionar. Los anchos elegidos se guardan en `localStorage`.
* Cada fila intenta resolver automáticamente el proyecto asociado utilizando el PID, el código personalizado o el título.
* Se muestra una nueva columna **Observaciones** a la derecha. Cada celda contiene un textarea editable que:
  * Rellena automáticamente las observaciones existentes del proyecto.
  * Permite escribir y guardar cambios al salir del campo o pulsar `Ctrl+Enter`/`Cmd+Enter`.
  * Informa del estado con mensajes «Pendiente de guardar», «Guardando…», «Guardado» o el error recibido.
  * Deshabilita la edición si la fila no está vinculada a ningún proyecto.
* Las observaciones se guardan mediante la ruta `POST /update_observations/<pid>` y se sincronizan con todos los componentes que comparten `PROJECT_DATA`.

### Proyectos

La pestaña **Proyectos** permite:

* Editar horas asignadas y trabajadores por fase con guardado automático.
* Cambiar la fecha de inicio planificada desde la tabla.
* Abrir la imagen adjunta del proyecto en una pestaña nueva.
* Borrar proyectos completos recalculando la planificación y anotando el resultado en la lista de conflictos.

### Vacaciones

Desde la pestaña **Vacaciones** se registran periodos de descanso. El calendario marca esos días en rojo, bloquea las asignaciones y permite eliminarlos haciendo clic sobre la celda. La lista muestra primero las vacaciones futuras y después las pasadas.

### Recursos

La vista **Recursos** lista todos los trabajadores con una casilla para mostrar u ocultar su fila en el calendario sin perder la configuración al recargar.

### Otras vistas

* **Notas**: formulario para crear notas destacadas. En el calendario aparece una línea roja y el texto en rojo intenso al final de la tabla. La pestaña dedicada permite borrarlas.
* **Gantt**, **Horas**, **Milestones**, **Tracker**, etc., reutilizan la misma información de proyectos para ofrecer diferentes perspectivas de la planificación.

## Comportamiento del calendario principal

* Las fases se colocan respetando el orden de trabajadores (Pilar, Joseba 1, Irene, Mikel, Iban, Joseba 2, Naparra, Unai, Fabio, Beltxa, Igor, Albi y Eneko).
* Los campos de fecha aceptan formatos `dd/mm` o `dd-mm`; si no se indica año se utiliza el actual.
* Al mover una fase manualmente, se guarda el nuevo inicio y se resalta la tarjeta con un borde negro al recargar. `Ctrl+Z` deshace el último movimiento.
* Las fases «Pedidos», «mecanizar» y «tratamiento» se gestionan con sus reglas específicas de capacidad descritas en el código: Irene puede solapar pedidos, mientras que mecanizado y tratamiento se reparten en jornadas de ocho horas consecutivas.
* El módulo de división de fases permite fraccionar horas y volver a unirlas posteriormente.

## Observaciones globales

La pestaña **Observaciones** sigue disponible para editar de forma masiva todos los textos guardados, pero ahora cualquier edición desde la tabla **Columna 1** se refleja inmediatamente gracias al mismo endpoint de actualización.

## Atajos y recordatorios

* Shift + rueda: desplazamiento horizontal por el calendario.
* El botón **Editar jornada laboral** despliega los selectores para ajustar las horas trabajadas (entre 1 y 9) por día. Los cambios se guardan y el calendario mantiene la posición tras recargar.
* Los fines de semana se muestran como una sola columna estrecha.
* Las tareas pueden arrastrarse entre días o a la columna **Fases sin planificar**. Se validan las vacaciones y el orden natural de fases antes de aceptar el movimiento.

## Desarrollo

El código principal vive en `app.py` y las vistas en `templates/`. Los estilos globales están en `static/style.css`. El repositorio no incluye pruebas automáticas; para validar cambios se recomienda ejecutar la aplicación y revisar manualmente las vistas afectadas.
