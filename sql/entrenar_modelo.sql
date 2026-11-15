-- Corre una vez por semana (consulta programada). Crear el modelo es lo único de BigQuery ML que NO entra
-- en el TiB gratuito (USD 312,50/TiB; esta tabla pesa unos pocos MiB). Un modelo por ESTRATO
-- (time_series_id_col): la densidad media de las 4 cámaras de cada altura, hora por hora (de día).
-- Reemplaza al LSTM en Azure ML de la versión original: con series cortas, ARIMA_PLUS es más adecuado,
-- se entrena en segundos y no necesita servidores.
CREATE OR REPLACE MODEL `${p}.ecocentinela.modelo_plagas`
OPTIONS (
  model_type = 'ARIMA_PLUS',
  time_series_timestamp_col = 'hora',
  time_series_data_col = 'densidad',
  time_series_id_col = 'estrato',
  data_frequency = 'HOURLY',
  horizon = 72,
  auto_arima = TRUE
) AS
SELECT estrato, TIMESTAMP_TRUNC(medido, HOUR) AS hora, AVG(densidad_est) AS densidad
FROM `${p}.ecocentinela.telemetria_limpia`
WHERE medido > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 21 DAY)
GROUP BY estrato, hora;
