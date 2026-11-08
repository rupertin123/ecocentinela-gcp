-- Calidad de datos por nodo y día.
-- Completitud: un nodo transmite cada 15 min, así que un día completo son 96 uplinks.
WITH t AS (
  SELECT DATE(medido, 'America/Argentina/Cordoba') AS dia, nodo_id,
         COUNT(*) AS filas, COUNT(DISTINCT evento_id) AS eventos_unicos,
         MIN(fcnt) AS fcnt_min, MAX(fcnt) AS fcnt_max,
         AVG(confianza) AS confianza_media, MIN(bateria_v) AS bateria_min,
         -- Las filas cargadas en lote (historia) tienen «recibido» muy posterior a «medido»: no son demora real.
         APPROX_QUANTILES(IF(TIMESTAMP_DIFF(recibido, medido, MINUTE) < 60,
                             TIMESTAMP_DIFF(recibido, medido, MILLISECOND), NULL), 100)[OFFSET(95)] AS demora_p95_ms
  FROM `${p}.ecocentinela.telemetria`
  WHERE invernadero != 'PRUEBA DE CARGA'
  GROUP BY dia, nodo_id
),
r AS (
  SELECT DATE(recibido, 'America/Argentina/Cordoba') AS dia, COUNT(*) AS rechazos
  FROM `${p}.ecocentinela.rechazos` GROUP BY dia
)
SELECT t.dia, t.nodo_id, t.filas, t.eventos_unicos,
       t.filas - t.eventos_unicos AS duplicados,
       ROUND(100 * t.eventos_unicos / 96, 1) AS completitud_pct,
       (t.fcnt_max - t.fcnt_min + 1) - t.eventos_unicos AS tramas_perdidas,
       ROUND(t.confianza_media, 3) AS confianza_media, t.bateria_min, t.demora_p95_ms,
       IFNULL(r.rechazos, 0) AS rechazos_del_dia
FROM t LEFT JOIN r USING (dia)
ORDER BY dia DESC, nodo_id
