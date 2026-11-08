-- Una fila por evento: Pub/Sub garantiza entrega «al menos una vez», así que puede haber repetidos.
SELECT * EXCEPT (n)
FROM (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY evento_id ORDER BY recibido) AS n
  FROM `${p}.ecocentinela.telemetria`
  WHERE invernadero != 'PRUEBA DE CARGA'
)
WHERE n = 1
