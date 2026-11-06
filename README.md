# Charts de sellos

Escribes el nombre de un sello y muestra todo lo que ha publicado, con vídeos de YouTube y la información de Last.fm.

## Para qué sirve

Para explorar el catálogo de un sello de un vistazo: un mosaico de portadas con vídeos de YouTube e información de Last.fm, guardado en tu servidor para no repetir consultas.

## Instalación individual

Necesitas Docker.

```bash
git clone https://github.com/volteret4/charts_sellos.git
```

```bash
cd charts_sellos && cp .env.example .env && mkdir -p data
```

```bash
docker build -t charts-sellos .
```

```bash
docker run -d --name charts-sellos --restart unless-stopped --env-file .env -e CHARTS_DATA_DIR=/app/data -v "$PWD/data:/app/data" -p 8792:8792 charts-sellos
```

Se abre en <http://localhost:8792>.

## Instalación desde REPEAT-ONE

Este módulo forma parte de [REPEAT-ONE](https://volteret4.github.io/repeat-one-web/), que permite elegir qué módulos instalar: [ver la instalación](https://github.com/volteret4/repeat-one-web#instalación). Allí se llama `charts_sellos`.

## Versión pública

Hay una [versión para navegador](https://volteret4.github.io/charts-web/sellos/) que no necesita servidor: guarda las búsquedas en tu navegador y usa tus propias claves.

## Cambiar a otro servicio

Con `CHARTS_SWITCH=Nombre:puerto,Nombre:puerto` en el `.env`, la barra lateral muestra un selector con los servicios de ese mismo host que estén respondiendo. Sin la variable no aparece.

## Cómo funciona

1. **Búsqueda**: se piden candidatos a MusicBrainz y Discogs. Se marca por defecto la coincidencia exacta de cada fuente, y puedes cambiarla porque hay muchos sellos homónimos.
2. **Catálogo**: se juntan los dos catálogos por artista + título. MusicBrainz manda en el tipo de disco. Lo que solo está en Discogs saca el tipo de su campo de formato, y un `12"` o `7"` sin más descripción se clasifica como EP o single por aproximación.
3. **Last.fm**: oyentes, escuchas, etiquetas, portada, wiki y canciones del disco; oyentes, escuchas, etiquetas, biografía y similares del artista. Con `LASTFM_USERNAME` también tus escuchas de cada disco. Se refresca a los 14 días.
4. **YouTube**: `yt-dlp` busca y guarda hasta 5 vídeos cuyo título o canal nombren disco y artista (o el sello, en recopilatorios de varios artistas). Si un disco solo está subido pista a pista, valen los vídeos del artista que nombren una de sus canciones o su número de catálogo. Si aún no se ha buscado un disco, se busca al pulsarlo.

Al añadir un sello eliges qué secciones traer (álbumes, EP, singles, recopilatorios…). El botón **Secciones** las cambia después: lo desmarcado desaparece y, al volver a marcarlo, no se repiten las consultas ya guardadas.

El grid aparece al terminar el catálogo y se va rellenando. Un trabajo interrumpido continúa al reiniciar, sin repetir lo guardado.

Hay dos colas independientes. La primera trae catálogo y Last.fm; al terminar, el sello pasa a la de YouTube. Así, mientras se buscan los vídeos de un sello, los siguientes ya van trayendo su información. YouTube va después porque aprovecha las canciones y la duración que da Last.fm.

## Desarrollo

Sin Docker y desde esta carpeta (lee `.env` si existe):

```bash
pip install -r requirements.txt && python app.py
```

| Variable | Para qué |
|---|---|
| `LASTFM_API_KEY` | Información de Last.fm |
| `DISCOGS_TOKEN` | Opcional: sin él solo se usa MusicBrainz |
| `LASTFM_USERNAME` | Opcional: tus escuchas de cada disco |
| `YT_WORKERS` | Búsquedas de YouTube simultáneas (2 por defecto). Cada una suma unas 1700 búsquedas/h desde la misma IP |
| `SETTINGS_PASSWORD` | Panel de configuración ⚙ |
| `CHARTS_DATA_DIR` | Carpeta de datos (por defecto `data/`) |

## Si YouTube bloquea las búsquedas

Si responde «Sign in to confirm you're not a bot», la cola de YouTube se pausa 30 minutos y reintenta sola; si sigue bloqueado, la pausa se duplica hasta un máximo de 4 horas. La cola de Last.fm sigue avanzando. Si no se resuelve, exporta las cookies en formato Netscape a `data/cookies.txt` y pulsa ↻ en cualquier elemento para reintentar ya: solo se buscan los discos que faltan.

## Limitaciones

- Dos ediciones con títulos distintos («Beat» y «Beat (Remastered)») cuentan como discos distintos.
- En Discogs, los recopilatorios pueden incluir discos ajenos en los que el sello solo licenció una pista.
- Un sello de miles de referencias tarda horas: YouTube se consulta despacio a propósito.
- Los iconos de «ya lo tengo» vienen del servicio `ya_lo_tengo` (puerto 8780); sin él, simplemente no salen.

## Código

- `app.py`: servidor sin dependencias web, trabajos en segundo plano y SQLite (`data/charts_sellos.db`).
- `static/app.js`: la web.
