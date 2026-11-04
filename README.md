# Charts de sellos

Visor al estilo de `charts_orpheus`, pero sin Orpheus: escribes el nombre de un sello y muestra todo lo que ha publicado, con vídeos de YouTube y la información de Last.fm.

## Cómo funciona

1. **Búsqueda**: se piden candidatos a MusicBrainz y Discogs. Se marca por defecto la coincidencia exacta de cada fuente, y puedes cambiarla porque hay muchos sellos homónimos.
2. **Catálogo**: se juntan los dos catálogos por artista + título. MusicBrainz manda en el tipo de disco. Lo que solo está en Discogs saca el tipo de su campo de formato, y un `12"` o `7"` sin más descripción se clasifica como EP o single por aproximación.
3. **Last.fm**: oyentes, escuchas, etiquetas, portada, wiki y canciones del disco; oyentes, escuchas, etiquetas, biografía y similares del artista. Con `LASTFM_USERNAME` también tus escuchas de cada disco. Se refresca a los 14 días.
4. **YouTube**: `yt-dlp` busca de uno en uno y guarda hasta 5 vídeos cuyo título o canal nombren disco y artista (o el sello, en recopilatorios de varios artistas). Si un disco solo está subido pista a pista, valen los vídeos del artista que nombren una de sus canciones o su número de catálogo. Si aún no se ha buscado un disco, se busca al pulsarlo.

Al añadir un sello eliges qué secciones traer (álbumes, EP, singles, recopilatorios…). El botón **Secciones** las cambia después: lo desmarcado desaparece y, al volver a marcarlo, no se repiten las consultas ya guardadas.

El grid aparece al terminar el catálogo y se va rellenando. Un trabajo interrumpido continúa al reiniciar, sin repetir lo guardado.

## Uso

```bash
./scripts/up.sh
```

Elige el perfil `charts_sellos`. La web queda en el puerto 8792.

Para desarrollo, sin Docker y desde esta carpeta (lee `.env` si existe):

```bash
pip install -r requirements.txt && python app.py
```

| Variable | Para qué |
|---|---|
| `LASTFM_API_KEY` | Información de Last.fm |
| `DISCOGS_TOKEN` | Opcional: sin él solo se usa MusicBrainz |
| `LASTFM_USERNAME` | Opcional: tus escuchas de cada disco |
| `SETTINGS_PASSWORD` | Panel de configuración ⚙ |
| `CHARTS_DATA_DIR` | Carpeta de datos (por defecto `data/`) |

## Si YouTube bloquea las búsquedas

Si responde «Sign in to confirm you're not a bot», el trabajo se detiene y el sello queda como «incompleto». Exporta las cookies en formato Netscape a `data/cookies.txt` y pulsa ↻: solo se buscan los discos que faltan.

## Limitaciones

- Dos ediciones con títulos distintos («Beat» y «Beat (Remastered)») cuentan como discos distintos.
- En Discogs, los recopilatorios pueden incluir discos ajenos en los que el sello solo licenció una pista.
- Un sello de miles de referencias tarda horas: YouTube se consulta despacio a propósito.
- Los iconos de «ya lo tengo» vienen del servicio `ya_lo_tengo` (puerto 8780); sin él, simplemente no salen.

## Código

- `app.py`: servidor sin dependencias web, trabajos en segundo plano y SQLite (`data/charts_sellos.db`).
- `static/app.js`: la web. El reproductor y los grupos vienen de `charts_orpheus`.
