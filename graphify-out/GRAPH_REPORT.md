# Graph Report - wise-backend  (2026-11-02)

## Corpus Check
- Corpus is ~27,643 words - fits in a single context window. You may not need a graph.

## Summary
- 756 nodes · 1904 edges · 32 communities (21 shown, 11 thin omitted)
- Extraction: 90% EXTRACTED · 10% INFERRED · 0% AMBIGUOUS · INFERRED: 194 edges (avg confidence: 0.95)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- Collection Stop & User Models
- Collection & Notification Models
- Reports & Leaderboard Routing
- Waste Records Management
- Route Geometry & Navigation
- Notification System & Endpoints
- Waste Classification & Taxonomy
- AI Waste Guide Management
- User Profile Management
- User Profile Schemas & Validation
- User Administration Routing
- Application Lifecycle & Startup
- Error Handling & Middleware
- Waste Prediction Endpoint
- Authentication Routing & Endpoints
- Database Session & History Schemas
- AI Model Inference Service
- Configuration & FCM Push Service
- Dashboard & Route Geometry Schemas
- Image Preprocessing Pipeline
- Rate Limiting Middleware
- Authentication & Token Service
- JWT Authentication & Verification
- Collection History Filtering
- Branding & Civic Entities
- Admin Authentication Routing

## God Nodes (most connected - your core abstractions)
1. `User` - 114 edges
2. `CollectionSchedule` - 35 edges
3. `Notification` - 24 edges
4. `WasteRecord` - 22 edges
5. `build_schedule_route()` - 19 edges
6. `generate_route_notification()` - 17 edges
7. `build_pdf()` - 17 edges
8. `get_db()` - 15 edges
9. `predict_waste_image()` - 14 edges
10. `create_manual_announcement()` - 14 edges

## Surprising Connections (you probably didn't know these)
- `run()` --calls--> `sync_auto_status_notifications()`  [INFERRED]
  apps/main.py → apps/services/collection_schedule.py
- `Recycling Rate Calculation Algorithm` --conceptually_related_to--> `Recyclable Waste Class`  [INFERRED]
  BACKEND_REPORTS_UPDATE.md → apps/ai_model/labels.txt
- `get_collection_history()` --uses--> `CollectionHistory`  [INFERRED]
  apps/services/collection_history.py → apps/models/collection_history.py
- `report_summary()` --uses--> `CollectionSchedule`  [INFERRED]
  apps/routers/reports.py → apps/models/collection_schedule.py
- `delete_collection_schedule()` --uses--> `CollectionSchedule`  [INFERRED]
  apps/services/collection_schedule.py → apps/models/collection_schedule.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Waste Prediction Inference Pipeline** — readme_predict_endpoint, readme_waste_classifier_service, readme_image_preprocessing_pipeline, readme_mobilenetv2_model [INFERRED 0.85]
- **Reports Export and Aggregation Flow** — backend_reports_update_overview, backend_reports_update_summary_endpoint, backend_reports_update_export_endpoint [INFERRED 0.85]
- **Waste Classification Category System** — apps_ai_model_labels_labels_file, apps_ai_model_labels_biodegradable, apps_ai_model_labels_recyclable, apps_ai_model_labels_hazardous [EXTRACTED 1.00]

## Communities (32 total, 11 thin omitted)

### Community 0 - "Collection Stop & User Models"
Cohesion: 0.06
Nodes (52): CollectionStop, User, add_stop(), change_schedule_status(), create_multiple_schedules(), create_schedule(), delete_single_stop(), get_route_preview_for_date() (+44 more)

### Community 1 - "Collection & Notification Models"
Cohesion: 0.06
Nodes (40): CollectionHistory, CollectionSchedule, DeviceToken, Notification, NotificationRead, get_activity(), get_collection_rail(), get_dashboard() (+32 more)

### Community 2 - "Reports & Leaderboard Routing"
Cohesion: 0.06
Nodes (25): export_report(), get_leaderboard(), report_summary(), derive_schedule_status(), _barangay_performance_rows(), build_excel(), build_pdf(), canvas_maker() (+17 more)

### Community 3 - "Waste Records Management"
Cohesion: 0.06
Nodes (23): WasteRecord, create_new_waste_record(), delete_existing_waste_record(), get_classification_stats(), get_waste_barangay_leaderboard(), get_waste_record(), list_waste_records(), update_existing_waste_record() (+15 more)

### Community 4 - "Route Geometry & Navigation"
Cohesion: 0.07
Nodes (24): RouteGeometry, get_route_directions(), get_schedule_geometry(), _validate_coordinates(), resolve_stop_coords(), _sanitize(), build_schedule_route(), chain_hash() (+16 more)

### Community 5 - "Notification System & Endpoints"
Cohesion: 0.09
Nodes (24): create_new_notification(), delete_existing_notification(), get_notification(), get_notification_settings(), list_notifications(), mark_read(), my_notifications(), notification_summary() (+16 more)

### Community 6 - "Waste Classification & Taxonomy"
Cohesion: 0.06
Nodes (33): Biodegradable Waste Class, Electronic Waste Class, Hazardous Waste Class, AI Model Waste Classification Labels List, Recyclable Waste Class, Residual Waste Class, Barangay Performance Report, Collection Calendar Report (+25 more)

### Community 7 - "AI Waste Guide Management"
Cohesion: 0.12
Nodes (16): AIGuide, create_new_ai_guide(), delete_existing_ai_guide(), get_ai_guide(), list_ai_guides(), update_existing_ai_guide(), AIGuideCreate, AIGuideListResponse (+8 more)

### Community 8 - "User Profile Management"
Cohesion: 0.10
Nodes (11): change_password(), _delete_old_image(), _ensure_profile_dir(), get_my_profile(), remove_profile_photo(), _serialize_user(), update_my_profile(), upload_profile_photo() (+3 more)

### Community 9 - "User Profile Schemas & Validation"
Cohesion: 0.13
Nodes (7): ChangePasswordRequest, ProfileUpdate, UserCreate, UserListItem, UserListResponse, UserResponse, UserUpdate

### Community 10 - "User Administration Routing"
Cohesion: 0.18
Nodes (11): create_new_user(), delete_existing_user(), get_user(), list_users(), update_existing_user(), create_user(), delete_user(), get_user_by_email() (+3 more)

### Community 11 - "Application Lifecycle & Startup"
Cohesion: 0.14
Nodes (9): test_connection(), ensure_schema(), lifespan(), root(), start_auto_status_worker(), run(), test_db(), get_health_status() (+1 more)

### Community 12 - "Error Handling & Middleware"
Cohesion: 0.13
Nodes (5): app_exception_handler(), AppException, generic_exception_handler(), integrity_error_handler(), validation_exception_handler()

### Community 13 - "Waste Prediction Endpoint"
Cohesion: 0.15
Nodes (6): predict_waste_image(), PredictionResponse, _remove_stored_file(), class_key(), save_waste_image(), validate_image_metadata()

### Community 14 - "Authentication Routing & Endpoints"
Cohesion: 0.22
Nodes (9): login(), refresh_token(), signup(), LoginRequest, LoginResponse, RefreshTokenRequest, SignupRequest, UserResponse (+1 more)

### Community 15 - "Database Session & History Schemas"
Cohesion: 0.18
Nodes (5): get_db(), CollectionHistoryListItem, CollectionHistoryListResponse, CollectionHistoryResponse, get_current_admin()

### Community 19 - "Dashboard & Route Geometry Schemas"
Cohesion: 0.21
Nodes (5): DashboardStats, DepotInfo, LineStringGeometry, RouteStopPosition, ScheduleRouteResponse

### Community 21 - "Rate Limiting Middleware"
Cohesion: 0.31
Nodes (3): get_client_ip(), get_rate_limit_config(), rate_limiting_middleware()

### Community 22 - "Authentication & Token Service"
Cohesion: 0.31
Nodes (3): authenticate_user(), login_user(), create_access_token()

### Community 23 - "JWT Authentication & Verification"
Cohesion: 0.25
Nodes (3): decode_access_token(), get_current_user(), get_optional_current_user()

### Community 26 - "Branding & Civic Entities"
Cohesion: 0.67
Nodes (3): City of Muntinlupa, WISE Logo, Smart Waste Management

## Knowledge Gaps
- **21 isolated node(s):** `Barangay Performance Report`, `Waste Trends Report`, `Route Efficiency Report`, `Environmental Impact Report`, `Comparative Performance Report` (+16 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 294 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **11 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `User` connect `Collection Stop & User Models` to `Collection & Notification Models`, `Reports & Leaderboard Routing`, `Waste Records Management`, `Route Geometry & Navigation`, `Notification System & Endpoints`, `AI Waste Guide Management`, `User Profile Management`, `User Administration Routing`, `Waste Prediction Endpoint`, `Authentication Routing & Endpoints`, `Database Session & History Schemas`, `Database Models & Engine Config`, `Authentication & Token Service`, `JWT Authentication & Verification`, `Collection History Filtering`, `Admin Authentication Routing`?**
  _High betweenness centrality (0.328) - this node is a cross-community bridge._
- **Are the 90 inferred relationships involving `User` (e.g. with `create_new_ai_guide()` and `delete_existing_ai_guide()`) actually correct?**
  _`User` has 90 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Barangay Performance Report`, `Waste Trends Report`, `Route Efficiency Report` to the rest of the system?**
  _21 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Collection Stop & User Models` be split into smaller, more focused modules?**
  _Cohesion score 0.062172284644194754 - nodes in this community are weakly interconnected._
- **Why does `WasteClassifierService` connect `AI Model Inference Service` to `Configuration & FCM Push Service`?**
  _High betweenness centrality (0.032) - this node is a cross-community bridge._
- **Are the 26 inferred relationships involving `CollectionSchedule` (e.g. with `report_summary()` and `delete_collection_schedule()`) actually correct?**
  _`CollectionSchedule` has 26 INFERRED edges - model-reasoned connections that need verification._
- **Should `Collection & Notification Models` be split into smaller, more focused modules?**
  _Cohesion score 0.058333333333333334 - nodes in this community are weakly interconnected._