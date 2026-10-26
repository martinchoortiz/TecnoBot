
SELECT id,
       channel_ref,
       phone,
       user_id,
       state,
       vehicle_slots,
       created_at,
       updated_at
FROM public.conversation
LIMIT 1000;

SELECT id,
       conversation_id,
       role,
       content,
       external_id,
       created_at
FROM public.message
LIMIT 1000;

SELECT id,
       created_at,
       source,
       error_type,
       http_status,
       message,
       details,
       conversation_id,
       channel_ref
FROM public.internal_error
LIMIT 1000;
