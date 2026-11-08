-- Corre todos los días. ML.FORECAST es «predicción»: entra en el TiB mensual gratuito de BigQuery.
CREATE OR REPLACE TABLE `${p}.ecocentinela.pronostico` AS
SELECT nodo_id, forecast_timestamp AS hora,
       GREATEST(forecast_value, 0) AS adultos_hoja,
       GREATEST(prediction_interval_lower_bound, 0) AS limite_inferior,
       prediction_interval_upper_bound AS limite_superior,
       CURRENT_TIMESTAMP() AS generado
FROM ML.FORECAST(MODEL `${p}.ecocentinela.modelo_mosca_blanca`,
                 STRUCT(48 AS horizon, 0.8 AS confidence_level));
