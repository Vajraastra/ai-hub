# Taller Anima · PoC

Taller local de arte para varios juegos. Cada proyecto guarda sus formatos, categorías y reglas
de organización. La biblioteca comparte estilos y piezas únicamente cuando se elige ese ámbito.

## Abrir

Desde la raíz del Hub, abrir `run_anima_studio.bat`. Prepara su entorno con uv y abre
`http://127.0.0.1:9754`; cerrar el servidor con Ctrl+C. En el Hub habitual, abrir la tarjeta
**Taller Anima** en **Aplicaciones**: el taller está integrado en `/anima-studio`.

Al abrir una pestaña del taller, ComfyUI se inicia automáticamente en segundo plano, sin otra
consola ni navegador. La interfaz indica cuándo está listo. Al cerrar la última pestaña que lo
arrancó, se cierra tras una breve gracia para tolerar recargas; espera los trabajos en curso.
Una instancia que ya estaba abierta manualmente o que usa otro propietario no se cierra.
Si se pierde el aviso de cierre y el Hub sigue abierto, la sesión vence y libera el backend.
Si el Hub termina inesperadamente, Windows cierra las apps que este inició y sus descendientes.

Para cargar una actualización, cerrar el Hub y volver a abrir `run.bat`. Si ComfyUI quedó abierto
de una ejecución anterior, **Aplicaciones → ComfyUI → Detener** permite cerrarlo aunque el Hub
actual no lo haya iniciado; después, volver a abrir el taller.

Lee `paths.models` de `hub/hub_config.json`. Para una copia de desarrollo puede definirse
`AI_HUB_CONFIG` con la ruta al archivo de configuración de la instalación. No copia ni cambia
pesos: muestra modelos y LoRAs/LoKr compatibles con Anima por sus encabezados safetensors.
También reconoce modelos Anima guardados en `checkpoints`, sin moverlos ni duplicarlos.
Los selectores de modelos y LoRAs abren una galería con miniaturas grandes, nombre y búsqueda
por archivo o etiquetas. Reutiliza previews y metadatos locales, incluido Model Vault en lectura.
Las pestañas de LoRAs corresponden a sus subcarpetas e incluyen una vista **Todos**.
El encoder Qwen3-0.6B y el VAE Qwen-Image se seleccionan de esa misma biblioteca.

Los prompts y negativos ofrecen las mismas sugerencias de tags que Painter: nombre, alias,
categoría y frecuencia. Escribir un tag separado por comas y elegir con ratón, flechas y
**Enter** o **Tab**; **Esc** cierra las sugerencias. También funcionan al editar piezas.
El autocompletado conserva el resto del texto y los pesos como `(blue hair:1.2)`.
Se puede combinar con texto libre o desactivar **Sugerencias de tags** desde Estilos.
En Anima inserta minúsculas y espacios en lugar de guiones bajos, conservando `score_*`;
Painter mantiene su formato habitual.

La base se comparte con Painter y se prepara bajo demanda. **Recargar** relee la base local;
si falta, **Descargar base de tags** la instala desde el taller. Solo esa descarga requiere
conexión. No cambia el modelo ni los ajustes de generación del estilo.

**Calidad y estructura para Anima** muestra una guía según el modelo elegido. Sus botones
añaden calidad o negativos al estilo de forma explícita y sin duplicados; elegir otro modelo
actualiza la recomendación, sin reescribir los prompts. Anima Base y WAI Anima 1.0 recomiendan
`masterpiece, best quality, score_7`; `safe` es una clasificación de contenido separada.
En Aesthetic, `masterpiece, best quality` es opcional y deben evitarse `score_*` en ambos prompts.
Son puntos de partida publicados por los autores, no una comparación de calidad medida aquí.

Orden de referencia: calidad/metadatos/época/clasificación, sujetos, personaje/serie/artista y
detalles de apariencia, acción, composición y entorno. Los artistas usan `@`. En texto natural,
la ficha oficial recomienda dos o más oraciones y describir la apariencia además del nombre.
Fuentes: [Anima — CircleStone Labs](https://huggingface.co/circlestone-labs/Anima/blob/main/README.md)
y [WAI Anima 1.0 — WAI0731](https://tensor.art/models/1003360262834670526).

## Recorrido

1. Crear un proyecto y ajustar categorías/formatos. Dungeon Tycoon es un perfil inicial editable.
2. El taller abre en **Taller → Crea tus elementos**. Elegir una tarjeta visible: personaje,
   outfit, pose, expresión, fondo u objeto. Describir ese diseño, elegir estilo/formato y pulsar
   **Guardar y generar prueba**. **Guardar sin generar** conserva un borrador.
3. Las pruebas aparecen arriba, en **Elige la imagen de tu elemento**. Compararlas y pulsar
   **Elegir imagen del elemento** en el resultado elegido; guardar la pieza para vincular esa
   imagen y sus ajustes. Su miniatura permitirá reconocer el diseño en el inventario.
4. En **Elige desde tu inventario**, buscar diseños, filtrar por tipo o mostrar favoritos.
   **Añadir a imagen** incorpora un elemento a la composición; **Quitar** lo retira de esa
   combinación sin borrar el diseño. **Editar** y **Variante** permiten continuar su trabajo.
5. En **¿Qué vas a componer?**, elegir el tipo de imagen. La zona de composición muestra sólo
   los elementos seleccionados que corresponden a ese tipo. Elegir el acabado desde la galería
   visual de estilos, ajustar el formato y pulsar **Revisar combinación** o **Generar imagen**.
   Encuadre, negativo y seed están en **Ajustes de la toma**, cerrado por defecto. Las recetas
   están en **Guardar o recuperar una combinación**.
6. En **Expresiones**, elegir un maestro del proyecto o cargar un PNG, pintar la región facial y
   ajustar la intensidad. El parche se compone sobre el original; conserva el alfa y todos los
   píxeles exteriores a la máscara final. Cada expresión debe partir del maestro de referencia.
7. Aceptar, rechazar o restaurar imágenes desde la galería. Los aceptados se organizan por
   proyecto/categoría/personaje/outfit; los rechazados permanecen recuperables hasta su limpieza
   explícita. **Borrar** elimina una imagen tras confirmar, sin esperar el plazo de rechazados.
   La limpieza por lotes sigue disponible en **Biblioteca y revisión → Rechazados**.
8. Las piezas tienen **Borrar** en la biblioteca y las recetas junto a su selector de combinaciones.
   Un recurso utilizado por otra entrada o revisión guardada se conserva: el aviso identifica
   la dependencia que debe quitarse primero. Para borrar un proyecto, quitar antes sus recursos
   propios y sus imágenes disponibles; los recursos compartidos se conservan.

El botón de estilo abre una galería con miniaturas y búsqueda por nombre, etiquetas, modelo o
LoRA. **Todos**, **Favoritos** y **Con muestras** filtran los resultados; se muestran 24 estilos
por vez y el botón de cargar más amplía la vista. La selección se conserva al buscar, por lo que
el recorrido es el mismo con unos pocos estilos o una biblioteca de 300. Si un estilo aún no
tiene una muestra, se identifica con un marcador. Esta galería se utiliza al componer, al probar
un elemento y al recuperar un estilo para calibrarlo.

Si hace falta preparar un acabado, abrir **Estilos** y elegir modelo, LoRAs, pesos, prompt de
acabado y sampling. La escena de prueba es independiente del prompt de estilo. Comparar muestras
con la misma seed y guardar el estilo. Para corregir un nombre, cargar el estilo, editarlo y
guardar una revisión; **Borrar estilo** retira uno creado por error.

Borrar imágenes elimina el PNG, sus variantes y su ficha. La ficha sólo se conserva de forma
temporal si hace falta completar la limpieza, para poder reintentar sin perder su procedencia.
Las pendientes y rechazadas se pueden borrar aunque sean muestras de un estilo: desaparecen de
sus miniaturas y selecciones, mientras el estilo conserva sus ajustes y revisiones. Los maestros
utilizados por otros resultados y las muestras aceptadas referenciadas quedan protegidos.
Las tarjetas desaparecen al confirmarse el borrado, incluso si falla la actualización posterior;
los avisos permanecen visibles al desplazarse por la página. Si quedan archivos bloqueados o
ComfyUI está ocupado, el aviso indica que la limpieza sigue pendiente.

Las seeds se conservan como texto para no perder precisión de 64 bits. El historial guarda
configuración efectiva, workflow, piezas, revisiones y procedencia. Cambiar una pieza o estilo
no modifica las generaciones anteriores. Importar/exportar perfiles conserva las revisiones
fijadas por sus recetas; los archivos de modelos e imágenes no se incluyen en ese JSON.

## Maestros, variantes y composiciones

En **Taller → Crea tus elementos**, elegir el tipo de diseño. También se pueden administrar
desde **Biblioteca y revisión → Nueva pieza**. Personaje, outfit, pose, expresión, fondo y objeto
tienen instrucciones propias.
El personaje admite un LoRA opcional de identidad; sus activadores se escriben en su prompt.
El modelo, los LoRAs de acabado y el sampling se siguen preparando en **Estilos**.

**Guardar y generar prueba** utiliza sólo la pieza elegida y el estilo guardado.
La prueba de outfit se presenta sobre un maniquí neutro; personaje usa una presentación base,
fondo una escena sin figuras y objeto una presentación aislada. Son instrucciones de prompt:
el objeto no obtiene transparencia automática. **Elegir imagen del elemento** permite guardar
la miniatura, la seed exacta y la configuración del resultado real en su pieza. Las pruebas de
creación se revisan en su zona superior y no se añaden automáticamente a la composición final.
Guardar no supone aprobación artística.

**Crear variante** hereda los valores y la revisión fijada del maestro. Cambiar prompt, seed
o vestuario en esa variante no modifica el maestro. Si conserva una prueba de origen, puede
reutilizar su estilo y sampling incluso tras guardar nuevas revisiones del estilo. Formato y
seed de la prueba siguen siendo elecciones explícitas. Una variante no se presenta como una
prueba nueva hasta generar y vincular su propio resultado.

Para la primera composición, seleccionar un personaje propone su seed. Cada prueba de pieza
conserva su propia seed; el resultado combinado utiliza una sola. **Usar composición**, desde
una generación de producción, recupera los bloques, piezas/revisiones y ajustes efectivos y los
mantiene fijados. Se puede sustituir la expresión. **Editar como nueva composición** permite
cambiar otras piezas conservando la configuración inicial. Los LoRAs iguales se aplican una vez;
pesos contradictorios requieren un ajuste explícito.

**Como maestro** prepara la edición facial: recupera la composición original, permite elegir
una expresión o escribir su bloque y pintar la máscara. Conserva la seed/configuración del
maestro y reemplaza sólo el bloque de expresión y su negativo. La composición CPU conserva
los píxeles exteriores a la máscara. Las recetas de expresiones fijadas no se guardan hasta
que el formato de receta incluya también maestro y máscara; conservar la imagen y su ficha.

En **Ajustar el control de pose**, activar ControlNet permite editar las 18 articulaciones del
esqueleto corporal, importar/exportar su JSON o cargar un mapa PNG con manos/rostro detallados.
El JSON OpenPose importado debe contener una sola persona. **Instalar control de esqueleto**
descarga una vez el peso Anima LLLite de unos 23 MB, fijado por versión y SHA256; después funciona
sin conexión. No requiere instalar un pack de nodos adicional: usa el nodo nativo del ComfyUI
del Hub. Fuerza e intervalo regulan su influencia; el control es experimental y necesita revisión
con el modelo elegido. Las piezas de pose guardan el mapa y sus ajustes para reutilizarlos.
En edición facial se conserva su procedencia, y el recorte facial utiliza la composición del
maestro en vez de aplicar el esqueleto completo. El editor de maniquí 3D queda pendiente.

## Datos locales

`data/` está excluido de Git. Contiene biblioteca, revisiones, historial, aceptados y rechazados.
Para respaldar o trasladar la biblioteca completa, conservar este directorio. Los metadatos JSON
acompañan al PNG; las expresiones incluyen maestro, parche y máscaras.
El borrado de estilos, piezas, recetas y proyectos elimina sus JSON y todas sus revisiones,
sin conservarlos en `.trash`. Las generaciones que sigan vigentes mantienen sus propios ajustes
de procedencia. Una operación de metadatos interrumpida se recupera al volver a abrir el taller.

Cada generación usa una carpeta propia en ComfyUI. Cuando el resultado queda guardado en la
biblioteca, se retiran sus entradas y salidas temporales; repetir seed reutiliza el cálculo sin
depender del PNG anterior. Las limpiezas pendientes se reintentan con el backend libre.
**Biblioteca y revisión → Limpiar archivos pendientes** permite reintentarlas de inmediato.
Los restos antiguos se reconocen por hashes y por el workflow, conservando archivos compartidos
con recursos vigentes. La comprobación rechaza enlaces y archivos de procedencia desconocida.

`ANIMA_STUDIO_DATA` permite elegir otro directorio de datos. El PoC organiza los recursos dentro
de esa biblioteca; la descarga PNG entrega el archivo con su nombre contextual. No escribe
automáticamente dentro del repositorio de un juego.

## Alcance

- Pose corporal por esqueleto y Anima LLLite experimental. Maniquí 3D pendiente; las piezas
  combinan prompts con su control visual opcional. Referencias IP-Adapter fuera de este flujo.
- Txt2img produce RGB: el requisito alfa de un formato no elimina el fondo. Las expresiones
  conservan el alfa que ya tiene su maestro. El recorte de fondos automático queda pendiente.
- La comprobación exterior a la máscara es técnica; rostro, transición y acabado requieren
  revisión humana. La seed por sí sola no garantiza identidad ni igualdad entre configuraciones.
- Calibración por pruebas individuales y comparación de galería; barridos automáticos pendientes.
- Coordinación con ComfyUI dentro del único proceso del Hub. Otros clientes y procesos están
  fuera de esa coordinación; no ejecutar simultáneamente el Hub completo y el servidor PoC.
- El plazo de retención es un filtro configurable para limpieza manual; no hay borrado por reloj.

## Pruebas offline

Antes de desconectarse, comprobar que estén instalados el entorno, los modelos y la base
de tags: en Estilos debe aparecer **tags disponibles**. Si falta la base, descargarla desde
el taller mientras haya conexión. Después, biblioteca, sugerencias y generación funcionan
localmente. Las miniaturas existentes se reutilizan; actualizar datos de Civitai requiere red.
Si se probarán poses con ControlNet, usar **Instalar control de esqueleto** mientras haya
conexión y comprobar que figure instalado antes de desconectarse. Esa descarga es independiente
de los modelos de generación; el editor de esqueleto no demuestra por sí solo que el control esté listo.

Tras actualizar el código, reiniciar el Hub o el taller independiente y recargar con **Ctrl+F5**.
Usar la biblioteca `apps/anima_studio/data` de la instalación habitual, no una copia de pruebas.

1. Abrir el Hub habitual y **Aplicaciones → Taller Anima**. Esperar **ComfyUI disponible**.
2. Abrir la galería de modelos y localizar WAI Anima. En LoRAs, buscar por nombre, archivo
   o etiqueta; comprobar miniatura, selección y peso independiente de cada LoRA.
   Comprobar las pestañas por carpeta. Escribir `blue_hair` en un prompt y elegir una sugerencia;
   repetir en negativos y en el diálogo de piezas, también dentro de un tag con peso.
   Verificar que Anima inserta `blue hair`, conserva `(blue hair:1.2)` y añade `@` a artistas.
   Revisar la guía de calidad del modelo y comprobar que añadirla dos veces no duplica tags.
   Al cambiar de modelo, la guía se actualiza y el prompt existente se conserva: revisar los
   scores al pasar a Aesthetic, donde el autor desaconseja `score_*`.
3. Calibrar con seed fija, guardar un estilo y volver a cargarlo. Revisar que modelo, LoRAs,
   pesos, dimensiones, prompts y seed se recuperen antes de generar otra prueba.
   Para comparar calidad, hacer una prueba sin los tags de calidad y otra con el preset del
   autor. Mantener iguales modelo, LoRAs/pesos, prompt restante, negativo, seed y sampling.
   Registrar cuál se prefiere por modelo/estilo: la guía no sustituye esta comparación visual.
4. En Taller, crear un elemento desde su tarjeta y usar **Guardar y generar prueba**. Comprobar
   que el resultado aparezca en **Elige la imagen de tu elemento**, elegirlo y guardar; buscarlo
   por su miniatura en el inventario. Probar filtros por tipo, búsqueda y favoritos. Añadir un
   personaje y un outfit a la composición y retirar uno con **Quitar**, sin borrarlo del inventario.
   Cambiar el tipo de imagen final a Fondos: comprobar que no se incorporen piezas del personaje
   y que recetas/resultados correspondan al tipo elegido. Abrir la galería de estilos desde la
   composición y la prueba de un elemento; comprobar búsqueda, filtros y cargar más sin perder
   la selección. En Expresiones, usar el mismo maestro y revisar rostro y exterior de la máscara.
   Crear una variante del personaje o del outfit y vincular su propia prueba: el maestro debe
   conservar su diseño y seed. Con una composición preparada, probar otro elemento con un estilo
   distinto y comprobar que la composición conserve sus piezas, estilo y seed. Generar una imagen
   combinada y usar **Usar composición** para recuperar sus ajustes y revisiones originales.
   Si se instaló el control de pose, guardar una pose, reutilizarla con otro outfit y revisar la
   obediencia del cuerpo; comparar también con el control desactivado a igual seed y configuración.
5. Aceptar y rechazar muestras; comprobar organización y fichas. Con datos desechables, borrar
   una prueba desde Estilos, un estilo erróneo, una pieza y una receta. Comprobar que las imágenes
   pendientes/rechazadas se borren aun como muestras guardadas, manteniendo los ajustes del estilo;
   comprobar que los maestros utilizados y muestras aceptadas referenciadas se protejan con aviso.
   Tras borrar, confirmar que las tarjetas y miniaturas desaparezcan también al actualizar y
   volver a abrir el taller; los estilos borrados deben salir de las galerías de elección y la biblioteca.
   Generar otra vez con la misma seed y ajustes después de borrar la prueba anterior: debe
   recuperarse el cálculo disponible sin errores por un PNG antiguo que ya no existe.
   Si aparece limpieza pendiente, esperar a que ComfyUI esté libre y usar **Limpiar archivos
   pendientes**. Confirmar que el aviso desaparezca al completarse la retirada de archivos.
6. Cerrar la última pestaña del taller. El ComfyUI iniciado automáticamente debe terminar
   tras sus trabajos y la breve gracia de cierre; una instancia manual o compartida se conserva.
7. Volver a abrir el taller y esperar **ComfyUI disponible**. Sin trabajos en curso, usar
   **Aplicaciones → ComfyUI → Detener**. Comprobar que ComfyUI termina y el Hub sigue respondiendo.
8. Esperar a que el taller indique que el backend se detuvo y pulsar **Reintentar**. Debe volver
   a quedar disponible; cerrar la última pestaña y comprobar otra vez su cierre automático.

Las pruebas y estilos del laboratorio de desarrollo no constituyen arte aprobado para un juego.

## Verificación sin GPU

Desde la raíz y con el Python del entorno del Hub:

```powershell
hub-webui/.venv/Scripts/python.exe apps/anima_studio/tests/check_library.py
hub-webui/.venv/Scripts/python.exe apps/anima_studio/tests/check_generation.py
hub-webui/.venv/Scripts/python.exe apps/anima_studio/tests/check_pieces.py
hub-webui/.venv/Scripts/python.exe apps/anima_studio/tests/check_composition.py
hub-webui/.venv/Scripts/python.exe apps/anima_studio/tests/check_composition_api.py
hub-webui/.venv/Scripts/python.exe apps/anima_studio/tests/check_pose_control.py
hub-webui/.venv/Scripts/python.exe apps/anima_studio/tests/check_api.py
hub-webui/.venv/Scripts/python.exe apps/anima_studio/tests/check_catalog_assets.py
hub-webui/.venv/Scripts/python.exe apps/anima_studio/tests/check_backend_artifacts.py
hub-webui/.venv/Scripts/python.exe apps/anima_studio/tests/check_storage.py
hub-webui/.venv/Scripts/python.exe hub-webui/tests/check_backend_sessions.py
hub-webui/.venv/Scripts/python.exe hub/tests/check_backend_lifecycle.py
hub-webui/.venv/Scripts/python.exe hub/tests/check_process_guard.py
hub-webui/.venv/Scripts/python.exe hub/tests/check_backend_stop_windows.py
hub-webui/.venv/Scripts/python.exe hub/tests/check_backend_http_windows.py
hub-webui/.venv/Scripts/python.exe hub-webui/tests/test_comfy_jobs.py -v
hub-webui/.venv/Scripts/python.exe hub-webui/tests/check_tag_engine.py
hub-webui/.venv/Scripts/python.exe hub-webui/tests/check_tag_routes.py
node --test hub-webui/tests/check_anima_catalog_ui.cjs hub-webui/tests/check_anima_tags.cjs hub-webui/tests/check_tag_autocomplete.cjs hub-webui/tests/check_anima_prompt_guide.cjs hub-webui/tests/check_anima_production.cjs hub-webui/tests/check_anima_workflow.cjs hub-webui/tests/check_anima_pieces.cjs hub-webui/tests/check_anima_pose.cjs hub-webui/tests/check_anima_workspace.cjs hub-webui/tests/check_anima_style_browser.cjs
```

Las integraciones Windows usan procesos de prueba y puertos libres, sin GPU ni instancias
existentes de ComfyUI. Incluyen el cierre inesperado del Hub y la respuesta HTTP tras detener
el backend. Las pruebas offline de arte anteriores siguen requiriendo revisión visual.
