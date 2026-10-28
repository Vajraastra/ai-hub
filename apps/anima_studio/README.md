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
2. En **Estilos**, elegir modelo, LoRAs, pesos, prompt de acabado y sampling. La escena de prueba
   es independiente del prompt de estilo. Comparar muestras con la misma seed y guardar el estilo.
   **Borrar** permite quitar una prueba fallida desde su propia tarjeta. Para corregir un nombre,
   cargar el estilo, editarlo y guardar una revisión; **Borrar estilo** retira uno creado por error.
3. En **Producción**, elegir primero qué generar: personajes, fondos, objetos, cartas o una
   categoría del proyecto. Cada opción abre sus piezas y herramientas; **Cambiar recurso** vuelve
   al menú. Elegir un estilo y formato, combinar las piezas y guardar una receta si se reutilizará.
4. En **Expresiones**, elegir un maestro del proyecto o cargar un PNG, pintar la región facial y
   ajustar la intensidad. El parche se compone sobre el original; conserva el alfa y todos los
   píxeles exteriores a la máscara final. Cada expresión debe partir del maestro de referencia.
5. Aceptar, rechazar o restaurar imágenes desde la galería. Los aceptados se organizan por
   proyecto/categoría/personaje/outfit; los rechazados permanecen recuperables hasta su limpieza
   explícita. **Borrar** elimina una imagen tras confirmar, sin esperar el plazo de rechazados.
   La limpieza por lotes sigue disponible en **Biblioteca y revisión → Rechazados**.
6. Las piezas tienen **Borrar** en la biblioteca y las recetas junto a su selector en producción.
   Un recurso utilizado por otra entrada o revisión guardada se conserva: el aviso identifica
   la dependencia que debe quitarse primero. Para borrar un proyecto, quitar antes sus recursos
   propios y sus imágenes disponibles; los recursos compartidos se conservan.

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

- Referencias IP-Adapter y control visual de pose 3D pendientes. Las piezas de pose son prompts.
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
4. En Producción, elegir Personajes y combinar personaje/outfit. Volver al menú y elegir Fondos:
   comprobar que no se incorporen piezas del personaje y que recetas/resultados correspondan al
   tipo elegido. En Expresiones, usar el mismo maestro y revisar rostro y exterior de la máscara.
5. Aceptar y rechazar muestras; comprobar organización y fichas. Con datos desechables, borrar
   una prueba desde Estilos, un estilo erróneo, una pieza y una receta. Comprobar que las imágenes
   pendientes/rechazadas se borren aun como muestras guardadas, manteniendo los ajustes del estilo;
   comprobar que los maestros utilizados y muestras aceptadas referenciadas se protejan con aviso.
   Tras borrar, confirmar que las tarjetas y miniaturas desaparezcan también al actualizar y
   volver a abrir el taller; los estilos borrados deben salir de ambos selectores y la biblioteca.
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
node --test hub-webui/tests/check_anima_catalog_ui.cjs hub-webui/tests/check_anima_tags.cjs hub-webui/tests/check_tag_autocomplete.cjs hub-webui/tests/check_anima_prompt_guide.cjs hub-webui/tests/check_anima_production.cjs hub-webui/tests/check_anima_workflow.cjs
```

Las integraciones Windows usan procesos de prueba y puertos libres, sin GPU ni instancias
existentes de ComfyUI. Incluyen el cierre inesperado del Hub y la respuesta HTTP tras detener
el backend. Las pruebas offline de arte anteriores siguen requiriendo revisión visual.
