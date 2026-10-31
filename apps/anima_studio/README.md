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
En **Estilos** se seleccionan también el encoder Qwen3-0.6B y el VAE Qwen-Image de esa biblioteca.

Los prompts y negativos ofrecen las mismas sugerencias de tags que Painter: nombre, alias,
categoría y frecuencia. Escribir un tag separado por comas y elegir con ratón, flechas y
**Enter** o **Tab**; **Esc** cierra las sugerencias. También funcionan al editar piezas.
El autocompletado conserva el resto del texto y los pesos como `(blue hair:1.2)`.
Se puede combinar con texto libre o desactivar **Sugerencias de tags** desde Estilos.
En Anima inserta minúsculas y espacios en lugar de guiones bajos, conservando `score_*`;
Painter mantiene su formato habitual.

**Prompts guardados** conserva configuraciones con nombre: prompt de estilo, negativo y escena
de prueba. **+ Guardar actuales** captura los tres textos tal como están y pide un nombre;
no hace falta guardar un estilo ni generar una imagen. Se conservan en la biblioteca local del
Hub y están disponibles en todos los proyectos después de reiniciar.

Buscar y seleccionar una entrada no modifica el editor. **Aplicar** recupera los tres textos,
sin cambiar checkpoint, LoRAs, pesos, seed ni sampling. **Actualizar seleccionado** reemplaza
una entrada de forma explícita; **+ Guardar actuales** siempre crea otra. Las actualizaciones
en conflicto avisan para recargar antes de sobrescribir. Borrar una entrada no borra estilos,
imágenes ni los textos abiertos. La biblioteca funciona sin conexión ni ComfyUI disponible.

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

1. **+ Proyecto** abre una calibración limpia con valores iniciales y nombre provisional.
   **Configurar** permite cambiar nombre, categorías y formatos más adelante; es opcional.
2. En **Taller → Crea tus elementos**, elegir una de las ocho tarjetas: personaje, personalidad,
   outfit, pose, expresión, fondo, objeto o escenario. El editor queda fijado al tipo elegido;
   para diseñar otro tipo, cerrar y elegir su tarjeta. **+ Nueva pieza** desde Biblioteca lleva
   a esta elección inicial. El editor abre sin pedir nombre ni maestro. Describir el contenido y, si hace falta, añadir
   LoRAs propios con sus pesos y activadores. Los ocho tipos, incluidos Personalidad/Escenario,
   pueden aportar LoRAs. El **Negativo propio** describe lo que se quiere evitar en esa pieza.
   Elegir un **Estilo para probar** y pulsar **Generar prueba**. El estilo impone checkpoint,
   LoRAs de acabado/pesos, encoder, VAE, sampling y calidad; se prepara en **Estilos**.
   Es una referencia de prueba: la pieza no queda vinculada a él.
3. Las imágenes aparecen en **Tus pruebas**. Cambiar el diseño y generar de nuevo sin guardar
   ni cerrar. **Elegir imagen del elemento** recupera el contenido de esa muestra; abrir
   **Guardar en el inventario**, poner nombre y guardar cuando se quiera conservar el diseño.
   La miniatura y su ficha documentan cómo se obtuvo la prueba, sin convertir su estilo,
   configuración o seed en requisitos de la pieza. También se puede guardar sin generar imagen.
4. En **Elige desde tu inventario**, buscar diseños, filtrar por tipo o mostrar favoritos.
   **Añadir a imagen** lleva la pieza al **Integrador**. **Editar** conserva su tipo y
   **Variante** abre explícitamente una derivación del mismo tipo, con maestro/revisión fijados; cambiar el estilo de prueba no modifica sus prompts ni sus LoRAs propios.
5. En la pestaña **Integrador**, elegir qué imagen se quiere componer y seleccionar explícitamente
   personaje, outfit, pose, expresión u otras piezas compatibles, además del estilo final.
   Para sprites de personaje se elige el encuadre: cuerpo entero, medio cuerpo o retrato.
   Las piezas aportan contenido y LoRAs; el estilo elegido aporta acabado, calidad y generación.
   Los negativos propios se suman al del estilo y a las exclusiones adicionales de la toma.
   Los formatos se filtran por recurso: personajes VN verticales, fondos horizontales y objetos
   cuadrados. **Revisar combinación** muestra el texto final antes de **Generar imagen**.
   Las recetas en **Guardar o recuperar una combinación** fijan una combinación concreta de
   piezas, estilo, encuadre y revisiones. El nombre sólo es necesario al guardar la receta.
6. En **Expresiones**, elegir una imagen de producción compatible o cargar un PNG, pintar la región facial y
   ajustar la intensidad. Las pruebas de piezas, calibraciones y rechazados no se ofrecen como
   maestros. En este modo sólo se ofrece el selector de expresión; las demás piezas se
   conservan desde el maestro. Maestro y máscara son necesarios para editar una imagen existente;
   probar una pieza de expresión por separado no los requiere. El parche se compone sobre el
   original; conserva el alfa y todos los píxeles exteriores a la máscara final. Cada expresión
   debe partir del maestro de referencia.
7. **Biblioteca y revisión** permite buscar y organizar diseños, revisar imágenes y recuperar
   composiciones. Aceptar, rechazar o restaurar imágenes desde la galería. Los aceptados se organizan por
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
con la misma seed. Las tarjetas grandes muestran modelo, LoRAs con sus pesos, seed, pasos, CFG,
sampler, scheduler y resolución; los prompts, encoder y VAE se consultan al desplegar los detalles.
**Guardar estilo** en una tarjeta abre el diálogo con los ajustes de esa muestra, aunque el
formulario de calibración ya haya cambiado. No hace falta definir nombre ni guardar antes de probar.
Por defecto crea un estilo nuevo: se pueden conservar varios acabados de distintas calibraciones
o de una misma muestra. La escena de prueba no se incorpora al prompt reutilizable.
Para corregir un nombre o actualizar un estilo, elegir explícitamente **Nueva revisión de…**
en ese diálogo; conserva las muestras previas y añade la elegida como primera miniatura.
Las muestras antiguas sin prompts separados lo indican y permiten definir el prompt del estilo.
**Borrar estilo seleccionado** retira uno creado por error. **+ Proyecto** crea un proyecto con
nombre provisional y abre directamente una calibración limpia, con ajustes iniciales y sin
formulario previo; conserva las muestras del anterior. **Configurar** permite renombrarlo y
desplegar opciones de producción y organización cuando hagan falta. Categorías, formatos,
requisito alfa, retención y nombres de archivo no determinan para qué se puede usar un estilo.

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

Una pieza reusable guarda su prompt de contenido, negativo propio, LoRAs con pesos y
activadores, y sus controles semánticos. No guarda un estilo obligatorio. La ficha de la imagen
usada como muestra conserva la procedencia de la prueba, incluida su configuración efectiva,
pero no determina cómo se renderiza la pieza después.

**Estilos** es la fuente de checkpoint, LoRAs de acabado/pesos, encoder, VAE, sampling y prompt
de calidad, tanto al probar piezas como en el Integrador. Una misma pieza puede probarse con A
y usarse después con B sin arrastrar el acabado de A. Al reabrir una pieza o crear una variante,
se utiliza el estilo actual de la sesión para nuevas pruebas; no se selecciona automáticamente
el de su muestra histórica. La seed de prueba tampoco se impone al Integrador.

Los ocho editores admiten LoRAs de contenido. Los activadores se conservan con cada LoRA y se
incorporan al bloque de su pieza cuando está habilitado. La influencia se ajusta sin modificar
los LoRAs de acabado del estilo. Los LoRAs iguales con el mismo peso se aplican una vez;
pesos contradictorios requieren una decisión explícita.

| Pieza | Contenido reusable | Sólo presentación de la prueba |
|---|---|---|
| Personaje | Fisonomía, rostro, cabello, cuerpo y rasgos distintivos. | Encuadre y vista del estudio. |
| Outfit | Prendas, materiales, colores y accesorios. | Vista sobre un maniquí neutro. |
| Pose | Postura, orientación/vista y control de esqueleto opcional. | Maniquí de referencia. |
| Expresión | Cambios de ojos, cejas, boca e intensidad. | Retrato independiente. |
| Fondo / Escenario | Lugar, iluminación, clima y espacio para personajes. | Lienzo horizontal de prueba. |
| Objeto | Construcción, materiales y vista del recurso. | Presentación aislada o como icono. |
| Personalidad | Actitud y presencia. | Retrato de referencia. |

Las opciones semánticas de pose, expresión, fondo/escenario y vista de objeto participan en el
prompt del Integrador. El encuadre/vista de prueba de personaje, la vista de maniquí del outfit
y la presentación de icono no se trasladan al sprite: el Integrador tiene su propio encuadre.
Las plantillas de prueba tampoco añaden maniquíes ni fondos neutros a una composición final.

**Generar prueba** usa el borrador abierto sin crear ni revisar una pieza del inventario.
No exige nombre; **Guardar en el inventario** sí lo pide. Hay una descripción y un negativo
propios, sin otra ventana para copiar calidad. El negativo del estilo se suma al generar:
cambiarlo en Estilos no reescribe los negativos de las piezas. Guardar desde una muestra no
copia el acabado o su negativo efectivo dentro de la descripción reusable.

**Crear variante** hereda el contenido y la revisión fijada del maestro. Cambiar su descripción,
LoRAs o controles no modifica el maestro. La prueba de origen permanece como referencia hasta
que se genere y vincule otra; no es una obligación de estilo ni de configuración.

El **Integrador** reúne los selectores de piezas, el estilo y el encuadre final. Se pueden
probar distintas combinaciones o cambiar de estilo sin reescribir los diseños. Una receta o
una generación concreta sí conserva sus revisiones y configuración para poder recuperarla.
**Usar composición** recupera esos valores congelados; **Editar como nueva composición**
permite elegir otras piezas o un estilo distinto. Las composiciones antiguas sin estilo
conservan sus ajustes históricos, sin inventar un estilo para sus piezas.

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
2. En **Estilos**, abrir la galería de modelos y localizar WAI Anima. En LoRAs, buscar por nombre, archivo
   o etiqueta; comprobar miniatura, selección y peso independiente de cada LoRA.
   Comprobar las pestañas por carpeta. Escribir `blue_hair` en un prompt y elegir una sugerencia;
   repetir en negativos y en el diálogo de piezas, también dentro de un tag con peso.
   Verificar que Anima inserta `blue hair`, conserva `(blue hair:1.2)` y añade `@` a artistas.
   Revisar la guía de calidad del modelo y comprobar que añadirla dos veces no duplica tags.
   Al cambiar de modelo, la guía se actualiza y el prompt existente se conserva: revisar los
   scores al pasar a Aesthetic, donde el autor desaconseja `score_*`.
3. Calibrar con seed fija y dos pesos distintos de LoRA; comparar los datos de ambas tarjetas.
   Guardar los textos con nombre en **Prompts guardados**, reiniciar el taller, crear otro proyecto
   y aplicarlos: los tres textos deben recuperarse sin cambiar modelo, LoRAs ni sampling.
   Guardar otra entrada, actualizar una explícitamente y buscarla por nombre; probar el borrado
   sólo con una entrada desechable, comprobando que conserva los textos del editor y sus estilos.
   Cambiar el formulario y guardar un estilo desde cada muestra: cada uno debe conservar sus
   ajustes originales. Guardar dos nombres desde una misma muestra debe crear estilos independientes.
   Volver a cargar los estilos y revisar modelo, pesos, dimensiones, prompts y seed.
   Probar una revisión explícita y comprobar que conserva las muestras previas. **+ Proyecto**
   debe abrir Estilos sin diálogo, con nombre provisional y ajustes iniciales, sin borrar el anterior.
   **Configurar** permite cambiar el nombre; sus opciones avanzadas no son requisitos de calibración.
   Para comparar calidad, hacer una prueba sin los tags de calidad y otra con el preset del
   autor. Mantener iguales modelo, LoRAs/pesos, prompt restante, negativo, seed y sampling.
   Registrar cuál se prefiere por modelo/estilo: la guía no sustituye esta comparación visual.
4. Preparar dos estilos A/B con acabados y LoRAs distintos. En Taller, crear personaje, outfit,
   pose y expresión y probarlos con A. Cada editor debe abrir sin pedir nombre ni maestro,
   mostrar su descripción/negativo propios y permitir añadir LoRAs, pesos y activadores.
   Checkpoint, encoder, VAE, sampling, acabado y calidad sólo se configuran en Estilos.
   Generar varias pruebas sin llenar el inventario de borradores; elegir una y guardarla con nombre.
   Reabrir las piezas y crear variantes: deben conservar contenido y LoRAs, sin forzar el estilo A
   ni copiar el negativo de calidad a su negativo propio. Probar también Fondo, Objeto,
   Personalidad y Escenario; revisar controles y tamaños útiles para cada tipo.
   Abrir cada tipo desde su tarjeta y comprobar que el editor no permita cambiar a otro tipo
   ni sustituir el borrador por otro maestro. Biblioteca → **+ Nueva pieza** debe volver a las
   tarjetas; **Variante** conserva su tipo y origen. En Expresiones, verificar que el selector
   de maestros no incluya calibraciones ni pruebas de outfits/poses/objetos.
   En **Integrador**, elegir explícitamente las cuatro piezas guardadas, el estilo B y cuerpo
   entero. Revisar la combinación: debe contener el contenido/activadores de cada pieza y la
   calidad/LoRAs/configuración de B; no debe arrastrar acabado de A, maniquíes del outfit ni
   encuadre de las pruebas. Generar y revisar el sprite. Cambiar a medio cuerpo/retrato y guardar
   una receta; al recuperarla, el encuadre debe aparecer una sola vez y sus revisiones conservarse.
   Cambiar entre A y B: comprobar que no se modifiquen las piezas ni sus pesos propios y que la
   seed del Integrador no cambie por seleccionar una pieza. Probar un LoRA compartido con peso
   igual y con pesos distintos: el primero debe aplicarse una vez; el segundo debe avisar.
   Comprobar que se sumen los negativos propios y el del estilo, sin perder exclusiones al añadir
   otra pieza. Las opciones semánticas de pose, intensidad, luz/clima/espacio y vista de objeto
   deben aparecer en su bloque; la presentación de prueba no debe pasar al resultado final.
   Probar búsquedas, filtros, favoritos y retirada con **Quitar**, sin borrar el recurso. Cambiar
   a Fondos/Objetos y comprobar que sólo se ofrezcan piezas y formatos compatibles. Recuperar
   un resultado con **Usar composición**, incluida una composición antigua sin estilo.
   En edición facial, elegir maestro, pintar máscara, cambiar expresión/intensidad y revisar
   rostro y exterior de la máscara. El maestro aporta sus ajustes sin recalibrarlo; los LoRAs
   de la expresión nueva reemplazan a los de la anterior. La revisión artística sigue pendiente.
   Con una composición preparada, probar otro elemento con un estilo distinto: el Integrador
   debe conservar su combinación abierta. Si hay control de pose instalado, reutilizar una pose
   con otro outfit y comparar la obediencia con/sin control a igual seed y configuración.
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
hub-webui/.venv/Scripts/python.exe apps/anima_studio/tests/check_piece_drafts.py
hub-webui/.venv/Scripts/python.exe apps/anima_studio/tests/check_specialized_editors.py
hub-webui/.venv/Scripts/python.exe apps/anima_studio/tests/check_piece_integration.py
hub-webui/.venv/Scripts/python.exe apps/anima_studio/tests/check_prompt_library.py
hub-webui/.venv/Scripts/python.exe apps/anima_studio/tests/check_prompt_api.py
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
$checks = @(Get-ChildItem hub-webui/tests/check_anima*.cjs) + @(Get-ChildItem hub-webui/tests/check_tag*.cjs)
node --test @($checks.FullName)
```

Las integraciones Windows usan procesos de prueba y puertos libres, sin GPU ni instancias
existentes de ComfyUI. Incluyen el cierre inesperado del Hub y la respuesta HTTP tras detener
el backend. Las pruebas offline de arte anteriores siguen requiriendo revisión visual.
