-- All 81Z structures in rows whose state is "Configured for Tape-in", with the LVS device data as a column.
-- Oracle / Atlantis schema (same tables as MetadataScalarISO/atlantis_db.py and sampleSQL).
--   TESTROW.STATUS = 9      -> "Configured for Tape-in" (StatusCode 9 in the Atlantis catalog service; sampleSQL uses it too)
--   TESTROW.LATESTFLAG = '1' -> latest version of each row
--   CADENCEDATABASE = 'X81'  -> project; 81Z libraries all end in x81z_x81_lay (xtrpfx81z_x81_lay, isox81z_x81_lay, ...)
--   LVS parameters are TESTSTRUCTUREPARAMETERS rows named lvsDeviceData / shortedPinPairs (pivoted to one row per structure).
-- Status codes seen in X81Z libraries: Definition, Edits in progress, Generated (2), Checked-in/out, Pending Dispo,
-- Custom, Configured for Tape-in (9). To list the code -> text pairs yourself:
--   SELECT tr.STATUS, COUNT(*) FROM TESTROW tr WHERE tr.LATESTFLAG = '1' AND tr.CADENCEDATABASE = 'X81' GROUP BY tr.STATUS;

SELECT
  tr.CADENCEDATABASE,
  tr.CADENCELIBRARY,
  tr.TESTROWNAME,
  tr.STATUS                      AS ROWSTATUS,
  ts.TESTSTRUCTID,
  ts.TESTSTRUCTNAME,
  ts.TESTSTRUCTCOMMENT,
  st.TEMPLATENAME,
  st.CATEGORY,
  MAX(CASE WHEN LOWER(p.PARAMETERNAME) = 'lvsdevicedata'   THEN p.PARAMETERVALUE END) AS LVSDEVICEDATA,
  MAX(CASE WHEN LOWER(p.PARAMETERNAME) = 'shortedpinpairs' THEN p.PARAMETERVALUE END) AS SHORTEDPINPAIRS
FROM TESTROW tr
JOIN TESTSTRUCTURE ts
  ON ts.TESTROWID = tr.TESTROWID
JOIN STRUCTURETEMPLATE st
  ON st.TEMPLATEID = ts.TSTEMPLATEID
LEFT JOIN TESTSTRUCTUREPARAMETERS p
  ON  p.TESTSTRUCTID = ts.TESTSTRUCTID
  AND LOWER(p.PARAMETERNAME) IN ('lvsdevicedata', 'shortedpinpairs')
WHERE tr.LATESTFLAG = '1'
  AND tr.STATUS = 9
  AND tr.CADENCEDATABASE = 'X81'
  AND LOWER(tr.CADENCELIBRARY) LIKE '%x81z\_x81\_lay' ESCAPE '\'
GROUP BY
  tr.CADENCEDATABASE, tr.CADENCELIBRARY, tr.TESTROWNAME, tr.STATUS,
  ts.TESTSTRUCTID, ts.TESTSTRUCTNAME, ts.TESTSTRUCTCOMMENT, ts.TSSEQUENCE,
  st.TEMPLATENAME, st.CATEGORY
ORDER BY tr.CADENCELIBRARY, tr.TESTROWNAME, ts.TSSEQUENCE, ts.TESTSTRUCTID;

-- Notes
--  * Add  AND tr.TESTROWNAME LIKE 'x81zs\_%' ESCAPE '\'  to keep only the x81zs_ rows (x81zc_ rows exist in col/con/misc libraries).
--  * Structures without LVS data (e.g. back-end templates) are kept with a NULL LVSDEVICEDATA; add
--    AND  <the MAX(CASE ...) expression> IS NOT NULL  through a HAVING clause to drop them.
--  * If PARAMETERVALUE is a CLOB in your schema, MAX() raises ORA-00932; wrap it as DBMS_LOB.SUBSTR(p.PARAMETERVALUE, 4000, 1).
--  * Export the result as CSV and pass it to the name check: python lvs_name_check.py --lvs <file>.csv
--    (TESTSTRUCTNAME, LVSDEVICEDATA and SHORTEDPINPAIRS are recognised as column names).
