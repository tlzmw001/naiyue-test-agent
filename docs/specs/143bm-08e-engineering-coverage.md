# S8-2：工程覆盖验收映射

本表与build/spec143bm-s8-matrices/coverage-final.json共同查阅。JSON逐项记录96条旧资产的实际入口/结果/版本边界，以及24条两维语义资产的待评状态；本表补齐不能靠短文本资产代表的机械边界。测试结果以同目录verification.json为准，不将节点存在当成行为通过。

| 要求 | 当前生产入口 | tests/ 下确定性断言节点 |
| --- | --- | --- |
| 完整消息优先/长消息延续 | build_batch | test_memory_input_units.py::test_oversized_turn_uses_complete_messages_before_splitting；test_memory_input_units.py::test_message_continuation_keeps_bounded_original_overlap |
| 自然边界/硬截片不可引用 | model_batch/binder | test_memory_input_units.py::test_hard_cut_never_gets_writable_evidence；test_memory_input_units.py::test_natural_scanner_keeps_multiline_quotes_and_code_lines |
| 工具父子关系/孤立结果 | build_batch/binder | test_memory_input_units.py::test_tool_binding_keeps_parent_context；test_memory_input_units.py::test_missing_tool_parent_is_context_only；test_memory_input_units.py::test_orphan_tool_overlap_stays_non_citable |
| 多span/不自动补辅助来源 | resolve/binder | test_memory_evidence_storage.py::test_two_spans_are_both_resolved；test_memory_input_units.py::test_context_is_not_automatically_added_to_formal_sources |
| 旧源清理与部分确认 | worker/commit | test_memory_program_owned_actions.py::test_worker_source_only_handles_missing_old_archive_without_retry；test_memory_program_owned_actions.py::test_source_only_real_commit_preserves_every_content_clock_and_cas |
| 七种关系/坏P/O/证据与时间 | MemoryJobs→A/B→commit | test_memory_engineering_matrices.py::test_worker_matrix_uses_complete_split_and_real_side_effects |
| TTL/新证据/遗忘抑制 | MemoryAccess→事务 | test_memory_lifecycle.py::test_candidate_requires_real_new_evidence_for_promotion_and_renewal；test_memory_lifecycle.py::test_forget_erases_every_projection_and_blocks_old_source |
| 来源越权/隐私/角色 | resolve/写入准入 | test_memory_lifecycle_storage.py::test_source_from_different_workspace_session_is_rejected；test_memory_lifecycle_storage.py::test_sensitive_pattern_checks_original_text_not_json_escapes；test_memory_lifecycle_storage.py::test_tool_observation_and_inference_do_not_become_user_declarations |
| CAS/预算与失败 | 比较分组/runner | test_memory_s3_review_fixes.py::test_version_changed_proposal_invalidates_shared_group；test_memory_s3_review_fixes.py::test_budget_skip_reports_partial_without_comparison_call |
| 迁移/回滚/原时钟 | 项目及个人迁移 | test_memory_space_time_migration.py::test_two_kind_migration_preserves_history_clocks_and_is_idempotent；test_memory_space_time_migration.py::test_migration_conflicts_sources_forgotten_and_rollback |
| 多事项/干扰/扫描与条件 | prepare_memory_parts | test_memory_engineering_matrices.py::test_recall_matrix_uses_complete_split_and_readonly_exact_sets；test_memory_query_plans.py::test_single_oversized_sentence_is_omitted_not_cut |
| 个人完整组/版本一致性 | prepare_memory_parts | test_memory_personal_conventions.py::test_all_four_complete_bodies_and_readonly；test_memory_personal_conventions.py::test_version_conflict_excludes_identity_from_both_groups |
| 工具参数/权限/旧调用 | tool→许可→API | test_memory_action_contract.py::test_execution_rejects_tampered_permit_before_api；test_memory_action_contract.py::test_old_flat_model_call_is_rejected_without_business_io |
| 新评分/分母/额外输出 | review CLI→评分 | test_memory_review_metrics.py::test_missing_comparisons_stay_in_denominator_and_frontend_is_na；test_memory_review_metrics.py::test_extra_unsupported_output_and_duplicate_are_counted |
| 缺usage/真实成本接线 | ProbeCallback/Budget | test_memory_evaluation_costs.py::test_partial_usage_and_failed_call_are_not_reported_as_zero；test_memory_space_time_evaluation.py::test_background_followup_uses_saved_memory_and_exactly_three_calls |

当前工程矩阵worker/recall各24例、三组各8；原calibration保持原断言，新组增加不同状态与故障组合。旧comparison的24个受控写入不等同B语义判断，12个历史预期不符明确列出；JSON中的替代映射只表示对应关系分支的机械覆盖，不能声称同语义家族等价或模型效果非退化。

已退役的七类语义口径、旧TTL/固定三条及抄写正文要求以S5/S6替代；旧资产/报告留存。检索升级延期。模型语义正确率、同协议旧/新对照、最终回答收益与独立质量留出仍属于S8-3/4。SQL/HTTP及独立等待/提交等未知成本保持未知，不用于推导费用或性能收益。
