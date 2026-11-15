-- Eficacia OBSERVADA de cada aplicación, hora por hora, a partir de los datos de los 12 nodos.
-- No hay parcela sin tratar, así que se arma un «testigo virtual»: la tendencia log-lineal de la plaga en
-- las 72 h previas a la aplicación, proyectada hacia adelante y acotada por la capacidad del ambiente.
-- E_obs = 1 - N_observada / N_testigo  (la misma idea que la fórmula de Henderson-Tilton).
-- Los valores de capacidad y tasa máxima son los de common/ecocentinela/modelo.py (una prueba los compara).
WITH plagas AS (
  SELECT * FROM UNNEST([
    STRUCT('superior' AS estrato, 45.0 AS capacidad, 0.11 AS tasa_max),
    STRUCT('medio' AS estrato, 14.0 AS capacidad, 0.09 AS tasa_max),
    STRUCT('inferior' AS estrato, 90.0 AS capacidad, 0.09 AS tasa_max)])
),
apl AS (
  SELECT * EXCEPT (n) FROM (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY aplicacion_id ORDER BY registrada DESC) AS n
    FROM `${p}.ecocentinela.aplicaciones`)
  WHERE n = 1
),
horas AS (  -- densidad media del estrato (4 cámaras) por hora
  SELECT estrato, TIMESTAMP_TRUNC(medido, HOUR) AS hora, AVG(densidad_est) AS densidad
  FROM `${p}.ecocentinela.telemetria_limpia`
  WHERE medido > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 120 DAY)
  GROUP BY estrato, hora
),
previo AS (  -- tendencia previa: ln(N) = b + r·x, con x en días (negativos) desde la aplicación
  SELECT a.aplicacion_id,
         COVAR_SAMP(x, y) / NULLIF(VAR_SAMP(x), 0) AS r,
         AVG(y) - COVAR_SAMP(x, y) / NULLIF(VAR_SAMP(x), 0) * AVG(x) AS b
  FROM apl a
  JOIN (SELECT estrato, hora, LN(GREATEST(densidad, 0.01)) AS y FROM horas) h
    ON h.estrato = a.estrato AND h.hora >= TIMESTAMP_SUB(a.aplicada_en, INTERVAL 72 HOUR) AND h.hora < a.aplicada_en
  CROSS JOIN UNNEST([TIMESTAMP_DIFF(h.hora, a.aplicada_en, MINUTE) / 1440.0 - 0.5]) AS x  -- mismo corrimiento de 12 h
  GROUP BY a.aplicacion_id
  HAVING COUNT(*) >= 12
)
SELECT a.aplicacion_id, a.estrato, a.producto, a.aplicada_en, h.hora,
       -- la densidad de cada nodo es la media móvil de 24 h: su «momento» real es 12 h antes
       TIMESTAMP_DIFF(h.hora, a.aplicada_en, MINUTE) / 1440.0 - 0.5 AS dias,
       h.densidad AS n_observada,
       LEAST(pl.capacidad, EXP(pr.b + LEAST(GREATEST(pr.r, 0), pl.tasa_max) *
             (TIMESTAMP_DIFF(h.hora, a.aplicada_en, MINUTE) / 1440.0 - 0.5))) AS n_testigo,
       1 - h.densidad / LEAST(pl.capacidad, EXP(pr.b + LEAST(GREATEST(pr.r, 0), pl.tasa_max) *
             (TIMESTAMP_DIFF(h.hora, a.aplicada_en, MINUTE) / 1440.0 - 0.5))) AS e_obs
FROM apl a
JOIN previo pr USING (aplicacion_id)
JOIN plagas pl ON pl.estrato = a.estrato
JOIN horas h ON h.estrato = a.estrato
 -- desde las 48 h: el derribe tarda ~1 día y la densidad de cada nodo es la media de las fotos de las 24 h
 -- previas, así que recién a las 48 h esa ventana no mezcla fotos del período de derribe
 AND h.hora >= TIMESTAMP_ADD(a.aplicada_en, INTERVAL 48 HOUR)
 AND h.hora < TIMESTAMP_ADD(a.aplicada_en, INTERVAL 21 DAY)
