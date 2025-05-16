
INSERT INTO public.aggregator("aggregator_id", "name", "created_time", "changed_time" ) VALUES (0, 'NULL AGGREGATOR', '2000-01-01 00:00:00Z', '2022-01-02 01:02:03.500'); -- This is supposed to be ID 0
INSERT INTO public.aggregator("aggregator_id", "name", "created_time", "changed_time") VALUES (1, 'CACTUS', '2000-01-01 00:00:00Z', '2022-01-03 01:02:03.500');

SELECT pg_catalog.setval('public.aggregator_aggregator_id_seq', 2, true);

-- See tests/data/certificates for how these were generated
INSERT INTO public.certificate("certificate_id", "created", "lfdi", "expiry") VALUES (1, '2023-01-01 01:02:03.500', '854d10a201ca99e5e90d3c3e1f9bc1c3bd075f3b', '2037-01-01 01:02:03'); -- certificate 1

SELECT pg_catalog.setval('public.certificate_certificate_id_seq', 2, true);

INSERT INTO public.aggregator_certificate_assignment("assignment_id", "certificate_id", "aggregator_id") VALUES (1, 1, 1);

