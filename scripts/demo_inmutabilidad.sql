-- Demostración de la inmutabilidad de audit_logs (RF-701).
-- Cada ataque va dentro de una transacción con una fila de control propia y termina en ROLLBACK:
-- no deja nada en la tabla, ni siquiera si el trigger no estuviera instalado.
--
-- Uso (desde la raíz del repositorio, con el contenedor de PostgreSQL arriba y sano):
--   docker exec -i ritech-postgres16 sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < scripts/demo_inmutabilidad.sql
--
-- Resultado esperado: dos INSERT aceptados y tres ERROR del trigger (UPDATE, DELETE y TRUNCATE).

\echo
\echo '== 1) Alterar un registro de auditoría (UPDATE): debe ser rechazado =='
BEGIN;
INSERT INTO audit_logs (id, timestamp, actor, action, ip_address, payload)
  VALUES (gen_random_uuid(), 0, 'SISTEMA', 'DEMO_INMUTABILIDAD', '127.0.0.1', '{}');
UPDATE audit_logs SET action = 'ALTERADO' WHERE action = 'DEMO_INMUTABILIDAD';
ROLLBACK;

\echo
\echo '== 2) Borrar un registro de auditoría (DELETE): debe ser rechazado =='
BEGIN;
INSERT INTO audit_logs (id, timestamp, actor, action, ip_address, payload)
  VALUES (gen_random_uuid(), 0, 'SISTEMA', 'DEMO_INMUTABILIDAD', '127.0.0.1', '{}');
DELETE FROM audit_logs WHERE action = 'DEMO_INMUTABILIDAD';
ROLLBACK;

\echo
\echo '== 3) Vaciar la tabla completa (TRUNCATE): debe ser rechazado =='
BEGIN;
TRUNCATE audit_logs;
ROLLBACK;

\echo
\echo '== Fin: se esperan 3 mensajes ERROR (RF-701) y ningún cambio en la tabla =='
