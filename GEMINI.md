# Guidelines & Standards for Oracle EBS & Database Querying

## 1. Oracle SQL Syntax Rules
- **Use Traditional Oracle `(+)` Syntax:** Always write outer joins using traditional Oracle `(+)` syntax in the `WHERE` clause instead of ANSI SQL (`LEFT JOIN`).
- **Prevent `ORA-01417`:** When outer joining a table (e.g. `pyt_activity_mapping_dtl m`), ensure all its join conditions link exclusively to a single driving table (e.g. `ra_customer_trx_lines_all l`) or constants/literals.
- **Direct Master Mapping (No `CASE WHEN` Hardcoding):** Map accounts directly to master setup tables (e.g. `apps.pyt_activity_mapping_dtl`, `apps.mst_discount_code_type`, `apps.g5_master_business_hierarchy`). Never infer or guess accounts with `CASE WHEN` when master tables exist.

## 2. Formatting & Structure
- Group the `WHERE` clause into clear numbered sections:
  1. `[1] AR Transactions & Distribution Linkage`
  2. `[2] Hospital / OU Master Lookup`
  3. `[3] Setup / Type Master Lookup`
  4. `[4] Master Activity Mapping with (+)`
  5. `[5] Filter Criteria`
- Maintain clean indentation and align aliases.

## 3. Strict Safety Protocol
- **PROD (`PYT_PROD_8000`) is READ-ONLY:**
  - Only `SELECT` statements are permitted on PROD.
  - NEVER execute `INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, or package compilation (`ALTER PACKAGE ... COMPILE`) on PROD.
  - All package compilation, modifications, or DML tests must be conducted on TEST (e.g., `TEST 8010`).
