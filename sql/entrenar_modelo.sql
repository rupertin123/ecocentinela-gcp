-- Corre una vez por semana (consulta programada). Crear el modelo es lo único de BigQuery ML que NO entra
-- en el TiB gratuito (USD 312,50/TiB; ~30 MiB por corrida ≈ USD 0,009). Semanal: ~USD 0,04/mes bruto.
-- Reemplaza al LSTM en Azure ML de la versión original:
-- con pocos datos y series cortas, ARIMA_PLUS es más adecuado, se entrena en segundos y no necesita
-- servidores. Un modelo por nodo (time_series_id_col).
CREATE OR REPLACE MODEL `${p}.ecocentinela.modelo_mosca_blanca`
OPTIONS (
  model_type = 'ARIMA_PLUS',
  time_series_timestamp_col = 'hora',
  time_series_data_col = 'adultos_hoja',
  time_series_id_col = 'nodo_id',
  data_frequency = 'HOURLY',
  horizon = 48,
  auto_arima = TRUE,
  holiday_region = 'AR'
) AS
SELECT nodo_id, TIMESTAMP_TRUNC(medido, HOUR) AS hora, AVG(adultos_hoja_est) AS adultos_hoja
FROM `${p}.ecocentinela.telemetria_limpia`
WHERE medido > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 21 DAY)
GROUP BY nodo_id, hora;
