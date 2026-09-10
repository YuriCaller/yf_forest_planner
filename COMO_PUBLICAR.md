# Como publicar el repositorio

Pasos exactos, una sola vez.

## 1. Crear el repositorio en GitHub

En la pantalla de creacion:

- **Nombre:** `yf_forest_planner`
- **Visibilidad:** Publico
- **Agregar README:** DESACTIVADO. El repositorio ya trae uno; si GitHub crea
  el suyo, el primer envio dara conflicto.
- **Agregar .gitignore:** No. Ya viene uno adaptado a complementos de QGIS.
- **Agregar licencia:** **GNU General Public License v2.0**

La licencia no es opcional: el repositorio de complementos de QGIS exige codigo
abierto, y sin archivo de licencia el complemento queda sin permiso de uso.
GPL v2 es la de QGIS y evita cualquier friccion.

**Deje que GitHub cree el archivo LICENSE.** No lo escriba a mano: el texto de
la GPL debe ser el oficial y literal, y una copia con erratas puede invalidar
la licencia. El complemento ya incluye su propio `LICENSE.txt` con el aviso de
copyright, la advertencia de uso y los creditos, que es lo que la GPL pide que
acompane al codigo; el texto completo lo aporta GitHub en la raiz.

## 2. Subir el contenido

Descomprima este paquete en una carpeta, abra una terminal ahi y ejecute:

    git init
    git branch -M main
    git add .
    git commit -m "Version inicial: complemento, manual e iconos"
    git remote add origin https://github.com/YuriCaller/yf_forest_planner.git
    git pull origin main --allow-unrelated-histories
    git push -u origin main

El `git pull` trae el archivo de licencia que creo GitHub. Si aparece un
conflicto, conserve ambos archivos.

## 3. Verificar los enlaces del manual

Abra en el navegador:

    https://github.com/YuriCaller/yf_forest_planner/blob/main/docs/README.md

Si carga, los botones de ayuda del complemento funcionaran. Si no, revise
`DOCS_BASE` en `yf_forest_planner/core/constants.py`.

## 4. Publicar la primera version

En GitHub, **Releases → Create a new release**:

- Etiqueta: `v0.1.0`
- Titulo: `Forest Planner 0.1.0 (experimental)`
- Adjunte el ZIP del complemento, que se genera con:

      cd yf_forest_planner/..
      zip -r yf_forest_planner.zip yf_forest_planner -x "*.pyc" "*__pycache__*"

  El ZIP debe contener la carpeta `yf_forest_planner/` en su raiz: asi lo
  espera el instalador de QGIS.

## 5. Despues, no ahora

- **Repositorio oficial de complementos de QGIS:** conviene esperar a usarlo en
  un plan operativo real antes de publicarlo ahi. Requiere cuenta en
  plugins.qgis.org y el complemento sigue marcado como experimental.
- **GitHub Pages con MkDocs:** el manual en Markdown ya se lee bien en GitHub.
  Migre a Pages si el manual crece y necesita buscador y navegacion lateral;
  entonces habra que actualizar `DOCS_BASE`.

## Mantenimiento

Cada correccion del manual se publica sola: los botones de ayuda apuntan a la
rama `main`, de modo que el usuario siempre lee la version vigente sin
reinstalar el complemento.

Al publicar una version nueva, actualice `version=` en `metadata.txt` y agregue
la entrada correspondiente en `changelog=`.
