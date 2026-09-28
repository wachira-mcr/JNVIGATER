
CREATE OR REPLACE PROCEDURE ar_transaction_validate(
    p_org_id     IN NUMBER,
    p_request_id IN NUMBER,
    errbuf      OUT VARCHAR2,
    p_commit_flag in VARCHAR2 default null
) IS

    v_duplicate_con VARCHAR2(1);
    v_con_err_msg VARCHAR2(4000);
    v_h_prefix VARCHAR2(10) := 'H:';
    v_l_prefix VARCHAR2(10) := 'L:';
    v_d_prefix VARCHAR2(10) := 'D:';

    V_RESPONSIBILITY_ID NUMBER := 0;
    V_APPLICATION_ID NUMBER := 0;

BEGIN
    -- Phase 0: Pre-computation
    BEGIN
        SELECT  max(frv.responsibility_id)
                ,max(frv.APPLICATION_ID)
        INTO    V_RESPONSIBILITY_ID,
                V_APPLICATION_ID
        FROM    apps.fnd_profile_options_vl        fpo,
                apps.fnd_responsibility_vl         frv,
                apps.fnd_profile_option_values     fpov,
                apps.hr_organization_units         hou
                ,FND_APPLICATION                   appl
        WHERE   1  =   1
        AND     hou.organization_id         =   p_org_id
        and     appl.APPLICATION_SHORT_NAME =   'AR'
        and     upper(frv.responsibility_name)  like  upper('%RECEIVABLES%')
        AND     fpov.profile_option_value   =   TO_CHAR (hou.organization_id)
        AND     fpo.profile_option_id       =   fpov.profile_option_id
        AND     fpo.user_profile_option_name = 'MO: Operating Unit'
        AND     frv.responsibility_id       =   fpov.level_value
        AND     frv.application_id          =   appl.application_id;
    EXCEPTION WHEN OTHERS THEN
        V_RESPONSIBILITY_ID := null;
        V_APPLICATION_ID    := null;
    END;

    APPS.FND_GLOBAL.APPS_INITIALIZE (G_USER_ID,V_RESPONSIBILITY_ID,V_APPLICATION_ID);

    BEGIN
        select 'D' DUPLICATED
        into v_duplicate_con
        from FND_CONC_REQ_SUMMARY_V     FCV
            ,FND_CONCURRENT_REQUESTS    FCR
            ,(  select argument3    v_date_from
                        ,argument4     v_date_to
                        ,argument6     v_org_id
                from FND_CONCURRENT_REQUESTS
                where request_id = p_request_id
            )   MAIN
        where 1=1
        and FCV.request_id = FCR.request_id
        and FCV.request_id <> p_request_id
        and nvl(FCV.phase_code,'P') in ('R','P')
        and program_short_name in
        (   'XXPLARINT015'
            ,'XXPLARINT015_HEAD'
            ,'PYTINT015REP'
            ,'PYTINT015REP_HEAD'
            ,'XXG5ARINT015'
            ,'XXG5ARINT015_HEAD'
        )
        and argument3   between MAIN.v_date_from and MAIN.v_date_to
        and argument4   between MAIN.v_date_from and MAIN.v_date_to
        and argument6   =   MAIN.v_org_id
        and rownum = 1;
    EXCEPTION
        WHEN NO_DATA_FOUND THEN v_duplicate_con := null;
        WHEN TOO_MANY_ROWS THEN v_duplicate_con := 'D';
        WHEN OTHERS THEN v_duplicate_con := 'E'; v_con_err_msg := sqlerrm;
    END;

    -- Phase 1: Bulk Header Validation
    UPDATE pyt_ar_trx_itf_head_tmp h
    SET 
        batch_source_id = (SELECT batch_source_id FROM ra_batch_sources_all bs WHERE bs.name = h.batch_source_name AND bs.org_id = p_org_id AND rownum = 1),
        set_of_books_id = (SELECT set_of_books_id FROM ap_system_parameters_all sp WHERE sp.org_id = p_org_id AND rownum = 1),
        customer_id = (SELECT customer_id FROM ra_customers c WHERE c.customer_number = h.customer_number AND rownum = 1),
        orig_system_bill_customer_id = (SELECT customer_id FROM ra_customers c WHERE c.customer_number = h.customer_number AND rownum = 1),
        orig_system_bill_address_id = (SELECT hcasa.cust_acct_site_id FROM hz_cust_accounts hca JOIN hz_cust_acct_sites_all hcasa ON hca.cust_account_id = hcasa.cust_account_id JOIN hz_cust_site_uses_all hcsu ON hcasa.cust_acct_site_id = hcsu.cust_acct_site_id WHERE hca.account_number = h.customer_number AND hcsu.site_use_code = h.site_use_code AND rownum=1),
        cust_trx_type_id = (SELECT cust_trx_type_id FROM ra_cust_trx_types_all ctt WHERE ctt.name = h.cust_trx_type_name AND ctt.org_id = p_org_id AND rownum=1),
        payment_method_id = (SELECT attribute2 FROM ra_cust_trx_types_all ctt WHERE ctt.name = h.cust_trx_type_name AND ctt.org_id = p_org_id AND rownum=1),
        term_id = CASE WHEN h.cust_trx_type_name LIKE '%INV%' THEN (SELECT term_id FROM ra_terms rt WHERE rt.name = h.term_name AND rownum=1) ELSE NULL END,
        main_code_name = h.header_attribute5,
        
        head_status = CASE 
            WHEN v_duplicate_con IN ('D', 'E') THEN 'ERROR_CON'
            WHEN (SELECT COUNT(*) FROM pyt_ar_trx_itf_head_tmp dup WHERE dup.request_id = p_request_id AND dup.interface_line_attribute1 = h.interface_line_attribute1 AND dup.intbatchno = h.intbatchno AND dup.org_id = h.org_id AND dup.customer_number = h.customer_number) > 1 THEN 'ERROR_HEADER'
            WHEN (SELECT batch_source_id FROM ra_batch_sources_all bs WHERE bs.name = h.batch_source_name AND bs.org_id = p_org_id AND rownum = 1) IS NULL THEN 'ERROR_HEADER'
            WHEN (SELECT set_of_books_id FROM ap_system_parameters_all sp WHERE sp.org_id = p_org_id AND rownum = 1) IS NULL THEN 'ERROR_HEADER'
            WHEN (SELECT customer_id FROM ra_customers c WHERE c.customer_number = h.customer_number AND rownum = 1) IS NULL THEN 'ERROR_HEADER'
            WHEN h.header_attribute5 IS NULL THEN 'ERROR_HEADER'
            WHEN (SELECT COUNT(*) FROM gl_period_statuses ps WHERE ps.period_name = h.trx_date AND rownum=1) = 0 THEN 'ERROR_HEADER'
            ELSE 'OK'
        END,
        err_code = CASE 
            WHEN v_duplicate_con IN ('D', 'E') THEN 'ERROR_CON' 
            WHEN (SELECT COUNT(*) FROM pyt_ar_trx_itf_head_tmp dup WHERE dup.request_id = p_request_id AND dup.interface_line_attribute1 = h.interface_line_attribute1 AND dup.intbatchno = h.intbatchno AND dup.org_id = h.org_id AND dup.customer_number = h.customer_number) > 1 THEN 'ERROR_HEADER'
            ELSE 'OK' 
        END,
        err_msg = CASE 
            WHEN v_duplicate_con = 'D' THEN v_h_prefix || 'Concurrent have parameter Duplicate'
            WHEN v_duplicate_con = 'E' THEN v_h_prefix || v_con_err_msg
            ELSE NULL 
        END,
        status = CASE WHEN v_duplicate_con IS NULL THEN 'VALIDATED' ELSE 'REJECT' END
    WHERE org_id = p_org_id AND request_id = p_request_id AND status IS NULL;

    -- Phase 2: Bulk Line Validation
    UPDATE pyt_ar_trx_itf_line_tmp l
    SET 
        set_of_books_id = (SELECT set_of_books_id FROM ap_system_parameters_all sp WHERE sp.org_id = p_org_id AND rownum = 1),
        reference_line_id = CASE WHEN l.cust_trx_type_name LIKE '%CM%' THEN (SELECT customer_trx_line_id FROM ra_customer_trx_lines_all WHERE interface_line_attribute1 = l.reference_line_attribute1 AND interface_line_attribute6 = l.interface_line_attribute6 AND rownum=1) ELSE NULL END,
        line_status = CASE 
            WHEN l.interface_line_context IS NULL THEN 'ERROR_LINE'
            WHEN l.interface_line_attribute1 IS NULL THEN 'ERROR_LINE'
            WHEN l.interface_line_attribute2 IS NULL THEN 'ERROR_LINE'
            WHEN l.interface_line_attribute3 IS NULL THEN 'ERROR_LINE'
            WHEN l.interface_line_attribute4 IS NULL THEN 'ERROR_LINE'
            WHEN l.interface_line_attribute5 IS NULL THEN 'ERROR_LINE'
            WHEN l.interface_line_attribute8 IS NULL THEN 'ERROR_LINE'
            WHEN l.interface_line_attribute9 IS NULL THEN 'ERROR_LINE'
            WHEN l.site_use_code IS NULL THEN 'ERROR_LINE'
            WHEN l.line_number IS NULL THEN 'ERROR_LINE'
            WHEN l.amount IS NULL THEN 'ERROR_LINE'
            WHEN l.cust_trx_type_name LIKE '%CM%' AND l.reference_line_context IS NULL THEN 'ERROR_LINE'
            WHEN l.cust_trx_type_name LIKE '%CM%' AND l.reference_line_attribute1 IS NULL THEN 'ERROR_LINE'
            ELSE 'OK' 
        END,
        err_msg = CASE 
            WHEN l.interface_line_context IS NULL THEN v_l_prefix || (SELECT message_text FROM fnd_new_messages WHERE message_name = 'XXSSBAR-0016' AND rownum=1)
            WHEN l.site_use_code IS NULL THEN v_l_prefix || (SELECT message_text FROM fnd_new_messages WHERE message_name = 'XXSSBAR-0030' AND rownum=1)
            ELSE NULL 
        END,
        status = 'VALIDATED'
    WHERE org_id = p_org_id AND request_id = p_request_id AND status IS NULL;

    -- Phase 3: Bulk Distribution Validation
    UPDATE pyt_ar_trx_itf_dist_tmp d
    SET 
        discount_type = (SELECT upper(discount_type) FROM mst_discount_code_type t JOIN g5_master_business_hierarchy h ON h.company_code = t.hospital_code WHERE h.OU_ID = p_org_id AND t.discount_code = d.interface_line_attribute8 AND rownum=1),
        activity_code = (SELECT activity_code FROM pyt_activity_mapping_dtl pam WHERE pam.segment3 = d.segment3 AND rownum=1),
        account_code = (SELECT account_code FROM pyt_activity_mapping_dtl pam WHERE pam.segment3 = d.segment3 AND rownum=1),
        code_combination_id = (SELECT code_combination_id FROM gl_code_combinations gcc WHERE gcc.segment1 = d.segment1 AND gcc.segment2 = d.segment2 AND gcc.segment3 = d.segment3 AND gcc.segment4 = d.segment4 AND gcc.segment5 = d.segment5 AND rownum = 1),
        dist_status = 'OK',
        status = 'VALIDATED'
    WHERE org_id = p_org_id AND request_id = p_request_id AND status IS NULL;
    
    -- Hybrid auto-create GL combinations
    FOR rec IN (
        SELECT DISTINCT d.segment1, d.segment2, d.segment3, d.segment4, d.segment5, h.trx_date, h.set_of_books_id, d.org_id
        FROM pyt_ar_trx_itf_dist_tmp d
        JOIN pyt_ar_trx_itf_head_tmp h ON d.interface_header_id = h.interface_header_id
        WHERE d.org_id = p_org_id AND d.request_id = p_request_id AND d.code_combination_id IS NULL
    ) LOOP
        DECLARE
            v_msg VARCHAR2(4000);
            v_status_combi VARCHAR2(10);
        BEGIN
            valid_combi_id(rec.set_of_books_id, rec.segment1, rec.segment2, rec.segment3, rec.segment4, rec.segment5, rec.trx_date, rec.org_id, v_msg, v_status_combi, p_commit_flag);
        END;
    END LOOP;

    -- Update Code Combinations after hybrid loop
    UPDATE pyt_ar_trx_itf_dist_tmp d
    SET code_combination_id = (SELECT code_combination_id FROM gl_code_combinations gcc WHERE gcc.segment1 = d.segment1 AND gcc.segment2 = d.segment2 AND gcc.segment3 = d.segment3 AND gcc.segment4 = d.segment4 AND gcc.segment5 = d.segment5 AND rownum = 1)
    WHERE org_id = p_org_id AND request_id = p_request_id AND code_combination_id IS NULL;

    -- Update Valid Rec Account for RL Customers
    UPDATE pyt_ar_trx_itf_dist_tmp d
    SET dist_status = 'ERROR_DIST',
        err_msg = v_d_prefix || 'Please correct the receivable account assignment'
    WHERE org_id = p_org_id AND request_id = p_request_id 
      AND d.interface_header_id IN (
          SELECT interface_header_id FROM pyt_ar_trx_itf_head_tmp h
          WHERE h.customer_number LIKE '%-RL-%'
      )
      AND get_rec_account_cache(p_org_id, (SELECT customer_number FROM pyt_ar_trx_itf_head_tmp h WHERE h.interface_header_id = d.interface_header_id)) = 0;

    -- Phase 4: Error Cascading
    UPDATE pyt_ar_trx_itf_line_tmp l
    SET dist_status = 'ERROR_DIST',
        status = 'REJECT'
    WHERE org_id = p_org_id
      AND request_id = p_request_id
      AND EXISTS (
          SELECT 1 FROM pyt_ar_trx_itf_dist_tmp d
          WHERE d.interface_line_id = l.interface_line_id
            AND d.dist_status <> 'OK'
      );

    UPDATE pyt_ar_trx_itf_head_tmp h
    SET line_status = 'ERROR_LINE',
        dist_status = 'ERROR_DIST',
        status = 'REJECT'
    WHERE org_id = p_org_id
      AND request_id = p_request_id
      AND EXISTS (
          SELECT 1 FROM pyt_ar_trx_itf_line_tmp l
          WHERE l.interface_header_id = h.interface_header_id
            AND (l.line_status <> 'OK' OR l.dist_status <> 'OK')
      );

    IF p_commit_flag = 'Y' THEN
        COMMIT;
    END IF;

EXCEPTION
    WHEN OTHERS THEN
        errbuf := SQLERRM;
END ar_transaction_validate;

CREATE OR REPLACE PROCEDURE ar_transaction_validate2(
    p_org_id     IN NUMBER,
    p_request_id IN NUMBER,
    errbuf      OUT VARCHAR2,
    p_commit_flag in VARCHAR2 default null
) IS

    v_duplicate_con VARCHAR2(1);
    v_con_err_msg VARCHAR2(4000);
    v_h_prefix VARCHAR2(10) := 'H:';
    v_l_prefix VARCHAR2(10) := 'L:';
    v_d_prefix VARCHAR2(10) := 'D:';

    V_RESPONSIBILITY_ID NUMBER := 0;
    V_APPLICATION_ID NUMBER := 0;

BEGIN
    -- Phase 0: Pre-computation
    BEGIN
        SELECT  max(frv.responsibility_id)
                ,max(frv.APPLICATION_ID)
        INTO    V_RESPONSIBILITY_ID,
                V_APPLICATION_ID
        FROM    apps.fnd_profile_options_vl        fpo,
                apps.fnd_responsibility_vl         frv,
                apps.fnd_profile_option_values     fpov,
                apps.hr_organization_units         hou
                ,FND_APPLICATION                   appl
        WHERE   1  =   1
        AND     hou.organization_id         =   p_org_id
        and     appl.APPLICATION_SHORT_NAME =   'AR'
        and     upper(frv.responsibility_name)  like  upper('%RECEIVABLES%')
        AND     fpov.profile_option_value   =   TO_CHAR (hou.organization_id)
        AND     fpo.profile_option_id       =   fpov.profile_option_id
        AND     fpo.user_profile_option_name = 'MO: Operating Unit'
        AND     frv.responsibility_id       =   fpov.level_value
        AND     frv.application_id          =   appl.application_id;
    EXCEPTION WHEN OTHERS THEN
        V_RESPONSIBILITY_ID := null;
        V_APPLICATION_ID    := null;
    END;

    APPS.FND_GLOBAL.APPS_INITIALIZE (G_USER_ID,V_RESPONSIBILITY_ID,V_APPLICATION_ID);

    BEGIN
        select 'D' DUPLICATED
        into v_duplicate_con
        from FND_CONC_REQ_SUMMARY_V     FCV
            ,FND_CONCURRENT_REQUESTS    FCR
            ,(  select argument3    v_date_from
                        ,argument4     v_date_to
                        ,argument6     v_org_id
                from FND_CONCURRENT_REQUESTS
                where request_id = p_request_id
            )   MAIN
        where 1=1
        and FCV.request_id = FCR.request_id
        and FCV.request_id <> p_request_id
        and nvl(FCV.phase_code,'P') in ('R','P')
        and program_short_name in
        (   'XXPLARINT015'
            ,'XXPLARINT015_HEAD'
            ,'PYTINT015REP'
            ,'PYTINT015REP_HEAD'
            ,'XXG5ARINT015'
            ,'XXG5ARINT015_HEAD'
        )
        and argument3   between MAIN.v_date_from and MAIN.v_date_to
        and argument4   between MAIN.v_date_from and MAIN.v_date_to
        and argument6   =   MAIN.v_org_id
        and rownum = 1;
    EXCEPTION
        WHEN NO_DATA_FOUND THEN v_duplicate_con := null;
        WHEN TOO_MANY_ROWS THEN v_duplicate_con := 'D';
        WHEN OTHERS THEN v_duplicate_con := 'E'; v_con_err_msg := sqlerrm;
    END;

    -- Phase 1: Bulk Header Validation
    UPDATE pyt_ar_trx_itf_head_tmp h
    SET 
        batch_source_id = (SELECT batch_source_id FROM ra_batch_sources_all bs WHERE bs.name = h.batch_source_name AND bs.org_id = p_org_id AND rownum = 1),
        set_of_books_id = (SELECT set_of_books_id FROM ap_system_parameters_all sp WHERE sp.org_id = p_org_id AND rownum = 1),
        customer_id = (SELECT customer_id FROM ra_customers c WHERE c.customer_number = h.customer_number AND rownum = 1),
        orig_system_bill_customer_id = (SELECT customer_id FROM ra_customers c WHERE c.customer_number = h.customer_number AND rownum = 1),
        orig_system_bill_address_id = (SELECT hcasa.cust_acct_site_id FROM hz_cust_accounts hca JOIN hz_cust_acct_sites_all hcasa ON hca.cust_account_id = hcasa.cust_account_id JOIN hz_cust_site_uses_all hcsu ON hcasa.cust_acct_site_id = hcsu.cust_acct_site_id WHERE hca.account_number = h.customer_number AND hcsu.site_use_code = h.site_use_code AND rownum=1),
        cust_trx_type_id = (SELECT cust_trx_type_id FROM ra_cust_trx_types_all ctt WHERE ctt.name = h.cust_trx_type_name AND ctt.org_id = p_org_id AND rownum=1),
        payment_method_id = (SELECT attribute2 FROM ra_cust_trx_types_all ctt WHERE ctt.name = h.cust_trx_type_name AND ctt.org_id = p_org_id AND rownum=1),
        term_id = CASE WHEN h.cust_trx_type_name LIKE '%INV%' THEN (SELECT term_id FROM ra_terms rt WHERE rt.name = h.term_name AND rownum=1) ELSE NULL END,
        main_code_name = h.header_attribute5,
        
        head_status = CASE 
            WHEN v_duplicate_con IN ('D', 'E') THEN 'ERROR_CON'
            WHEN (SELECT COUNT(*) FROM pyt_ar_trx_itf_head_tmp dup WHERE dup.request_id = p_request_id AND dup.interface_line_attribute1 = h.interface_line_attribute1 AND dup.intbatchno = h.intbatchno AND dup.org_id = h.org_id AND dup.customer_number = h.customer_number) > 1 THEN 'ERROR_HEADER'
            WHEN (SELECT batch_source_id FROM ra_batch_sources_all bs WHERE bs.name = h.batch_source_name AND bs.org_id = p_org_id AND rownum = 1) IS NULL THEN 'ERROR_HEADER'
            WHEN (SELECT set_of_books_id FROM ap_system_parameters_all sp WHERE sp.org_id = p_org_id AND rownum = 1) IS NULL THEN 'ERROR_HEADER'
            WHEN (SELECT customer_id FROM ra_customers c WHERE c.customer_number = h.customer_number AND rownum = 1) IS NULL THEN 'ERROR_HEADER'
            WHEN h.header_attribute5 IS NULL THEN 'ERROR_HEADER'
            WHEN (SELECT COUNT(*) FROM gl_period_statuses ps WHERE ps.period_name = h.trx_date AND rownum=1) = 0 THEN 'ERROR_HEADER'
            ELSE 'OK'
        END,
        err_code = CASE 
            WHEN v_duplicate_con IN ('D', 'E') THEN 'ERROR_CON' 
            WHEN (SELECT COUNT(*) FROM pyt_ar_trx_itf_head_tmp dup WHERE dup.request_id = p_request_id AND dup.interface_line_attribute1 = h.interface_line_attribute1 AND dup.intbatchno = h.intbatchno AND dup.org_id = h.org_id AND dup.customer_number = h.customer_number) > 1 THEN 'ERROR_HEADER'
            ELSE 'OK' 
        END,
        err_msg = CASE 
            WHEN v_duplicate_con = 'D' THEN v_h_prefix || 'Concurrent have parameter Duplicate'
            WHEN v_duplicate_con = 'E' THEN v_h_prefix || v_con_err_msg
            ELSE NULL 
        END,
        status = CASE WHEN v_duplicate_con IS NULL THEN 'VALIDATED' ELSE 'REJECT' END
    WHERE org_id = p_org_id AND request_id = p_request_id AND status IS NULL;

    -- Phase 2: Bulk Line Validation
    UPDATE pyt_ar_trx_itf_line_tmp l
    SET 
        set_of_books_id = (SELECT set_of_books_id FROM ap_system_parameters_all sp WHERE sp.org_id = p_org_id AND rownum = 1),
        reference_line_id = CASE WHEN l.cust_trx_type_name LIKE '%CM%' THEN (SELECT customer_trx_line_id FROM ra_customer_trx_lines_all WHERE interface_line_attribute1 = l.reference_line_attribute1 AND interface_line_attribute6 = l.interface_line_attribute6 AND rownum=1) ELSE NULL END,
        line_status = CASE 
            WHEN l.interface_line_context IS NULL THEN 'ERROR_LINE'
            WHEN l.interface_line_attribute1 IS NULL THEN 'ERROR_LINE'
            WHEN l.interface_line_attribute2 IS NULL THEN 'ERROR_LINE'
            WHEN l.interface_line_attribute3 IS NULL THEN 'ERROR_LINE'
            WHEN l.interface_line_attribute4 IS NULL THEN 'ERROR_LINE'
            WHEN l.interface_line_attribute5 IS NULL THEN 'ERROR_LINE'
            WHEN l.interface_line_attribute8 IS NULL THEN 'ERROR_LINE'
            WHEN l.interface_line_attribute9 IS NULL THEN 'ERROR_LINE'
            WHEN l.site_use_code IS NULL THEN 'ERROR_LINE'
            WHEN l.line_number IS NULL THEN 'ERROR_LINE'
            WHEN l.amount IS NULL THEN 'ERROR_LINE'
            WHEN l.cust_trx_type_name LIKE '%CM%' AND l.reference_line_context IS NULL THEN 'ERROR_LINE'
            WHEN l.cust_trx_type_name LIKE '%CM%' AND l.reference_line_attribute1 IS NULL THEN 'ERROR_LINE'
            ELSE 'OK' 
        END,
        err_msg = CASE 
            WHEN l.interface_line_context IS NULL THEN v_l_prefix || (SELECT message_text FROM fnd_new_messages WHERE message_name = 'XXSSBAR-0016' AND rownum=1)
            WHEN l.site_use_code IS NULL THEN v_l_prefix || (SELECT message_text FROM fnd_new_messages WHERE message_name = 'XXSSBAR-0030' AND rownum=1)
            ELSE NULL 
        END,
        status = 'VALIDATED'
    WHERE org_id = p_org_id AND request_id = p_request_id AND status IS NULL;

    -- Phase 3: Bulk Distribution Validation
    UPDATE pyt_ar_trx_itf_dist_tmp d
    SET 
        discount_type = (SELECT upper(discount_type) FROM mst_discount_code_type t JOIN g5_master_business_hierarchy h ON h.company_code = t.hospital_code WHERE h.OU_ID = p_org_id AND t.discount_code = d.interface_line_attribute8 AND rownum=1),
        activity_code = (SELECT activity_code FROM pyt_activity_mapping_dtl pam WHERE pam.segment3 = d.segment3 AND rownum=1),
        account_code = (SELECT account_code FROM pyt_activity_mapping_dtl pam WHERE pam.segment3 = d.segment3 AND rownum=1),
        code_combination_id = (SELECT code_combination_id FROM gl_code_combinations gcc WHERE gcc.segment1 = d.segment1 AND gcc.segment2 = d.segment2 AND gcc.segment3 = d.segment3 AND gcc.segment4 = d.segment4 AND gcc.segment5 = d.segment5 AND rownum = 1),
        dist_status = 'OK',
        status = 'VALIDATED'
    WHERE org_id = p_org_id AND request_id = p_request_id AND status IS NULL;
    
    -- Hybrid auto-create GL combinations
    FOR rec IN (
        SELECT DISTINCT d.segment1, d.segment2, d.segment3, d.segment4, d.segment5, h.trx_date, h.set_of_books_id, d.org_id
        FROM pyt_ar_trx_itf_dist_tmp d
        JOIN pyt_ar_trx_itf_head_tmp h ON d.interface_header_id = h.interface_header_id
        WHERE d.org_id = p_org_id AND d.request_id = p_request_id AND d.code_combination_id IS NULL
    ) LOOP
        DECLARE
            v_msg VARCHAR2(4000);
            v_status_combi VARCHAR2(10);
        BEGIN
            valid_combi_id(rec.set_of_books_id, rec.segment1, rec.segment2, rec.segment3, rec.segment4, rec.segment5, rec.trx_date, rec.org_id, v_msg, v_status_combi, p_commit_flag);
        END;
    END LOOP;

    -- Update Code Combinations after hybrid loop
    UPDATE pyt_ar_trx_itf_dist_tmp d
    SET code_combination_id = (SELECT code_combination_id FROM gl_code_combinations gcc WHERE gcc.segment1 = d.segment1 AND gcc.segment2 = d.segment2 AND gcc.segment3 = d.segment3 AND gcc.segment4 = d.segment4 AND gcc.segment5 = d.segment5 AND rownum = 1)
    WHERE org_id = p_org_id AND request_id = p_request_id AND code_combination_id IS NULL;

    -- Update Valid Rec Account for RL Customers
    UPDATE pyt_ar_trx_itf_dist_tmp d
    SET dist_status = 'ERROR_DIST',
        err_msg = v_d_prefix || 'Please correct the receivable account assignment'
    WHERE org_id = p_org_id AND request_id = p_request_id 
      AND d.interface_header_id IN (
          SELECT interface_header_id FROM pyt_ar_trx_itf_head_tmp h
          WHERE h.customer_number LIKE '%-RL-%'
      )
      AND get_rec_account_cache(p_org_id, (SELECT customer_number FROM pyt_ar_trx_itf_head_tmp h WHERE h.interface_header_id = d.interface_header_id)) = 0;

    -- Phase 4: Error Cascading
    UPDATE pyt_ar_trx_itf_line_tmp l
    SET dist_status = 'ERROR_DIST',
        status = 'REJECT'
    WHERE org_id = p_org_id
      AND request_id = p_request_id
      AND EXISTS (
          SELECT 1 FROM pyt_ar_trx_itf_dist_tmp d
          WHERE d.interface_line_id = l.interface_line_id
            AND d.dist_status <> 'OK'
      );

    UPDATE pyt_ar_trx_itf_head_tmp h
    SET line_status = 'ERROR_LINE',
        dist_status = 'ERROR_DIST',
        status = 'REJECT'
    WHERE org_id = p_org_id
      AND request_id = p_request_id
      AND EXISTS (
          SELECT 1 FROM pyt_ar_trx_itf_line_tmp l
          WHERE l.interface_header_id = h.interface_header_id
            AND (l.line_status <> 'OK' OR l.dist_status <> 'OK')
      );

    IF p_commit_flag = 'Y' THEN
        COMMIT;
    END IF;

EXCEPTION
    WHEN OTHERS THEN
        errbuf := SQLERRM;
END ar_transaction_validate2;
/
