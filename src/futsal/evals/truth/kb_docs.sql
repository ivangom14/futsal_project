-- Documentos de la base de conocimiento con versión vigente indexada y fragmentos.
SELECT DISTINCT d.doc_key
FROM kb_documents d
JOIN kb_document_versions v ON v.document_id = d.id
WHERE v.is_current AND v.index_status = 'indexed'
  AND EXISTS (SELECT 1 FROM kb_chunks c WHERE c.version_id = v.id)
ORDER BY d.doc_key
