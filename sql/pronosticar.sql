-- Corre todos los días. ML.FORECAST es «predicción»: entra en el TiB mensual gratuito de BigQuery.
CREATE OR REPLACE TABLE `${p}.ecocentinela.pronostico` AS
SELECT estrato, forecast_timestamp AS hora,
       GREATEST(forecast_value, 0) AS densidad,
       GREATEST(prediction_interval_lower_bound, 0) AS limite_inferior,
       prediction_interval_upper_bound AS limite_superior,
       CURRENT_TIMESTAMP() AS generado
FROM ML.FORECAST(MODEL `${p}.ecocentinela.modelo_plagas`,
                 STRUCT(72 AS horizon, 0.8 AS confidence_level));
