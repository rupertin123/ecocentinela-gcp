-- Una fila por aplicación: ajuste de E(t) = E0·exp(-k·t) a la eficacia observada (mínimos cuadrados sobre
-- ln E) y fecha estimada en la que el lote queda DESCUBIERTO (E < 50 %). Con menos de 3 días de datos queda
-- sólo la estimación bibliográfica (e0_bibliografia, k_bibliografia). Es la versión SQL de eficacia.ajustar().
WITH apl AS (
  SELECT * EXCEPT (n) FROM (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY aplicacion_id ORDER BY registrada DESC) AS n
    FROM `${p}.ecocentinela.aplicaciones`)
  WHERE n = 1
),
puntos AS (
  SELECT aplicacion_id, dias, LN(e_obs) AS y
  FROM `${p}.ecocentinela.eficacia_puntos`
  WHERE e_obs > 0.02 AND e_obs < 0.995
),
ajuste AS (
  SELECT aplicacion_id,
         COUNT(DISTINCT CAST(ROUND(dias) AS INT64)) AS dias_con_datos,
         COUNT(*) AS puntos,
         -COVAR_SAMP(dias, y) / NULLIF(VAR_SAMP(dias), 0) AS k,
         EXP(AVG(y) - COVAR_SAMP(dias, y) / NULLIF(VAR_SAMP(dias), 0) * AVG(dias)) AS e0,
         POW(CORR(dias, y), 2) AS r2
  FROM puntos GROUP BY aplicacion_id
)
SELECT a.aplicacion_id, a.estrato, a.plaga, a.producto, a.principio_activo, a.aplicada_en, a.origen, a.ciclo,
       a.e0_bibliografia, a.k_bibliografia,
       TIMESTAMP_ADD(a.aplicada_en, INTERVAL CAST(86400 * LN(a.e0_bibliografia / 0.5) / a.k_bibliografia AS INT64) SECOND)
         AS descubierto_bibliografia,
       IFNULL(j.dias_con_datos, 0) AS dias_con_datos, j.puntos,
       IF(j.dias_con_datos >= 3 AND j.k > 0, LEAST(j.e0, 1.0), NULL) AS e0_campo,
       IF(j.dias_con_datos >= 3 AND j.k > 0, j.k, NULL) AS k_campo,
       IF(j.dias_con_datos >= 3 AND j.k > 0, j.r2, NULL) AS r2,
       IF(j.dias_con_datos >= 3 AND j.k > 0 AND j.e0 > 0.5,
          TIMESTAMP_ADD(a.aplicada_en, INTERVAL CAST(86400 * LN(LEAST(j.e0, 1.0) / 0.5) / j.k AS INT64) SECOND),
          NULL) AS descubierto_campo,
       CURRENT_TIMESTAMP() AS calculado
FROM apl a LEFT JOIN ajuste j USING (aplicacion_id)
