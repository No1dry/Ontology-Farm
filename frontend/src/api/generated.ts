export interface paths {
    "/api/v1/approvals": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Approvals */
        get: operations["listApprovals"];
        put?: never;
        /** Submit */
        post: operations["submitApproval"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/approvals/preview": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Preview */
        post: operations["previewApproval"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/approvals/{approval_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Approval */
        get: operations["getApproval"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/approvals/{approval_id}/decisions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Decide */
        post: operations["decideApproval"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/artifacts": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Upload */
        post: operations["uploadArtifact"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/artifacts/evidence": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Create Evidence */
        post: operations["createEvidence"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/artifacts/evidence/{id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Evidence */
        get: operations["getEvidence"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/artifacts/uploads": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Create Upload */
        post: operations["createArtifactUpload"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/artifacts/uploads/{upload_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Upload Status */
        get: operations["getArtifactUpload"];
        put?: never;
        post?: never;
        /** Cancel Upload */
        delete: operations["cancelArtifactUpload"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/artifacts/uploads/{upload_id}/complete": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Complete Upload */
        post: operations["completeArtifactUpload"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/artifacts/uploads/{upload_id}/parts/{number}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /** Upload Part */
        put: operations["putArtifactUploadPart"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/artifacts/{version_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get */
        get: operations["getArtifact"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/artifacts/{version_id}/content": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Content */
        get: operations["downloadArtifact"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/artifacts/{version_id}/links": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Link */
        post: operations["linkArtifact"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/artifacts/{version_id}/reviews": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Review */
        post: operations["reviewArtifact"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/conflicts": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Conflicts */
        get: operations["listConflicts"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/conflicts/{id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Conflict */
        get: operations["getConflict"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/conflicts/{id}/refresh": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Refresh Conflict */
        post: operations["refreshConflictCandidates"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/conflicts/{id}/review": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Review Conflict */
        post: operations["reevaluateConflictRuling"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/conflicts/{id}/rulings": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Rulings */
        get: operations["listConflictRulings"];
        put?: never;
        /** Resolve Conflict */
        post: operations["resolveConflict"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/geometry": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Geometry List */
        get: operations["listGeometry"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/geometry/{object_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Geometry Get */
        get: operations["getObjectGeometry"];
        /** Geometry Put */
        put: operations["updateObjectGeometry"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/grants": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Grants */
        get: operations["listGrants"];
        put?: never;
        /** Create Grant */
        post: operations["createGrant"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/grants/{grant_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        /** Revoke Grant */
        delete: operations["revokeGrant"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/graph/neighborhood": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Graph Neighborhood */
        get: operations["getGraphNeighborhood"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/graph/proofs/{assertion_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Assertion Proof */
        get: operations["getAssertionProof"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/groups": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Groups */
        get: operations["listGroups"];
        put?: never;
        /** Create Group */
        post: operations["createGroup"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/groups/{group_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        /** Update Group */
        patch: operations["updateGroup"];
        trace?: never;
    };
    "/api/v1/groups/{group_id}/members": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Members */
        get: operations["listGroupMembers"];
        /** Set Members */
        put: operations["setGroupMembers"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/health/overview": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Health */
        get: operations["getHealthOverview"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/history": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** History */
        get: operations["listHistory"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/history/{resource_id}/diff": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Diff */
        get: operations["diffRevisions"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/history/{resource_id}/revisions/{revision}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Revision */
        get: operations["getRevision"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/identity-operations": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Identity Operations */
        get: operations["listIdentityOperations"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/identity-operations/{operation_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Identity Operation */
        get: operations["getIdentityOperation"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/identity/aliases/{id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Resolve Identity Alias */
        get: operations["resolveIdentityAlias"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/identity/merges": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Merge Identities */
        post: operations["mergeIdentities"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/identity/merges/preview": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Preview Identity Merge */
        post: operations["previewIdentityMerge"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/imports/apply": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Apply Import */
        post: operations["applyImport"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/imports/preview": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Preview Import */
        post: operations["previewImport"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/imports/previews/{id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Import Preview */
        get: operations["getImportPreview"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/imports/previews/{id}/rows": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Preview Rows */
        get: operations["listImportPreviewRows"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/imports/{id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Import */
        get: operations["getImport"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/imports/{id}/rows": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Import Rows */
        get: operations["listImportRows"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/jobs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Jobs */
        get: operations["listJobs"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/jobs/{job_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Job */
        get: operations["getJob"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/jobs/{job_id}/pause": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Pause */
        post: operations["pauseJob"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/jobs/{job_id}/resume": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Resume */
        post: operations["resumeJob"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/jobs/{job_id}/retry": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Retry */
        post: operations["retryJob"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/links": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Links */
        get: operations["listLinks"];
        put?: never;
        /** Create Link */
        post: operations["createLink"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/links/{id}/withdraw": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Withdraw Link */
        post: operations["withdrawLink"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/location-observations": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Location Observation Post */
        post: operations["registerLocationObservation"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/maps/resources": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Resources */
        get: operations["listMapResources"];
        put?: never;
        /** Create Resource */
        post: operations["createMapResource"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/maps/resources/{id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Resource */
        get: operations["getMapResource"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        /** Update Resource */
        patch: operations["updateMapResource"];
        trace?: never;
    };
    "/api/v1/maps/resources/{id}/archive": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Archive */
        get: operations["getMapArchive"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/maps/resources/{id}/assets/{asset_path}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Asset */
        get: operations["getMapAsset"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/maps/resources/{id}/style.json": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Style */
        get: operations["getMapStyle"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/maps/resources/{id}/tiles/{z}/{x}/{y}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Tile */
        get: operations["getMapTile"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/objects": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Objects */
        get: operations["listObjects"];
        put?: never;
        /** Create Object */
        post: operations["createObject"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/objects/{id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Object */
        get: operations["getObject"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        /** Update Object */
        patch: operations["updateObject"];
        trace?: never;
    };
    "/api/v1/objects/{id}/assertions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Object Assertions */
        get: operations["listObjectAssertions"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/objects/{id}/deactivate": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Deactivate */
        post: operations["deactivateObject"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/objects/{id}/form-schema": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Object Form */
        get: operations["getObjectFormSchema"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/objects/{object_id}/location-observations": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Location Observations Get */
        get: operations["listLocationObservations"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/objects/{object_id}/spatial-assets": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Spatial Assets Get */
        get: operations["listSpatialAssets"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/query/exports": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Semantic Export */
        post: operations["createSemanticExport"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/query/sparql": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Sparql Query */
        post: operations["executeSparql"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/roles": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Roles */
        get: operations["listRoles"];
        put?: never;
        /** Create Role */
        post: operations["createRole"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/roles/{role_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        /** Update Role */
        patch: operations["updateRole"];
        trace?: never;
    };
    "/api/v1/rules/bundles": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Stage Rule Configuration */
        post: operations["stageRuleConfiguration"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/rules/impact": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Preview Rule Impact */
        post: operations["previewRuleImpact"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/rules/impact/{id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Rule Impact */
        get: operations["getRuleImpact"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/rules/versions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Rule Versions */
        get: operations["listRuleVersions"];
        put?: never;
        /** Create Rule Version */
        post: operations["createRuleVersion"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/rules/versions/{id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Rule Version */
        get: operations["getRuleVersion"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/rules/{id}/publish": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Publish Rules */
        post: operations["publishRules"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/schema/drafts": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Drafts */
        get: operations["listSchemaDrafts"];
        put?: never;
        /** Create Draft */
        post: operations["createSchemaDraft"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/schema/drafts/imports": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Import Draft */
        post: operations["importSemanticDraft"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/schema/drafts/{id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Draft */
        get: operations["getSchemaDraft"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        /** Update Draft */
        patch: operations["updateSchemaDraft"];
        trace?: never;
    };
    "/api/v1/schema/drafts/{id}/form-schema": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Draft Form */
        get: operations["getDraftFormSchema"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/schema/drafts/{id}/inheritance": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Inheritance */
        get: operations["getDraftInheritance"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/schema/drafts/{id}/submit": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Submit */
        post: operations["submitSchemaDraft"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/schema/drafts/{id}/validate": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Validate */
        post: operations["validateSchemaDraft"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/schema/releases": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Releases */
        get: operations["listSchemaReleases"];
        put?: never;
        /** Publish */
        post: operations["publishSchemaRelease"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/schema/releases/{id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Release */
        get: operations["getSchemaRelease"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/schema/releases/{id}/diff": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Diff */
        get: operations["diffSchemaRelease"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/schema/releases/{id}/form-schema": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Released Form */
        get: operations["getReleasedFormSchema"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/session": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Session */
        get: operations["getSession"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/session/callback": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Callback */
        get: operations["sessionCallback"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/session/login": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Login */
        get: operations["sessionLogin"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/session/logout": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Logout */
        post: operations["logoutSession"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/session/refresh": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Refresh */
        post: operations["refreshSession"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/sources": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Sources */
        get: operations["listSources"];
        put?: never;
        /** Create Source */
        post: operations["createSource"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/sources/{id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Source */
        get: operations["getSource"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/sources/{id}/connection": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Connection */
        get: operations["getSourceConnection"];
        /** Configure Connection */
        put: operations["configureSourceConnection"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/sources/{id}/credentials": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /** Set Credentials */
        put: operations["setSourceCredentials"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/sources/{id}/mapping": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Mapping */
        get: operations["getSourceMapping"];
        /** Save Mapping */
        put: operations["saveSourceMapping"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/sources/{id}/schedule": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Schedule */
        get: operations["getSourceSchedule"];
        /** Configure Schedule */
        put: operations["configureSourceSchedule"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/sources/{id}/test": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Test Connection */
        post: operations["testSourceConnection"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/spatial-assets": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Spatial Asset Post */
        post: operations["registerSpatialAsset"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/users": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Users */
        get: operations["listUsers"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/users/invitations": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Invite User */
        post: operations["inviteUser"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/users/{user_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        /** Update User */
        patch: operations["updateUser"];
        trace?: never;
    };
    "/api/v1/validation": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Validate */
        post: operations["validateFacts"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/health/live": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Live */
        get: operations["live_health_live_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/health/ready": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Ready */
        get: operations["ready_health_ready_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
}
export type webhooks = Record<string, never>;
export interface components {
    schemas: {
        /** AlignmentRule */
        "AlignmentRule-Input": {
            /** Class Iri */
            class_iri: string;
            /** Confirmed */
            confirmed: boolean;
            /** Digest */
            digest: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Key Paths */
            key_paths: string[];
            /**
             * Normalizers
             * @default []
             */
            normalizers: components["schemas"]["RuleExpr-Input"][];
            scope: components["schemas"]["ResourceScope"];
        };
        /** AlignmentRule */
        "AlignmentRule-Output": {
            /** Class Iri */
            class_iri: string;
            /** Confirmed */
            confirmed: boolean;
            /** Digest */
            digest: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Key Paths */
            key_paths: string[];
            /**
             * Normalizers
             * @default []
             */
            normalizers: components["schemas"]["RuleExpr-Output"][];
            scope: components["schemas"]["ResourceScope"];
        };
        /** ApiErrorDetail */
        ApiErrorDetail: {
            /** Code */
            code: string;
            /** Details */
            details: unknown | null;
            /** Message */
            message: string;
            /** Request Id */
            request_id: string;
        };
        /** ApplyImportCommand */
        ApplyImportCommand: {
            /** Approval Id */
            approval_id?: string | null;
            /** Expected Revision */
            expected_revision: number;
            /** Input Digest */
            input_digest: string;
            /**
             * Preview Id
             * Format: uuid
             */
            preview_id: string;
        };
        /** ApprovalDecision */
        ApprovalDecision: {
            /** Comment */
            comment: string;
            /**
             * Decision
             * @enum {string}
             */
            decision: "approve" | "reject";
        };
        /** ApprovalDetail */
        ApprovalDetail: {
            /** Baseline */
            baseline: {
                [key: string]: number;
            };
            /** Change Digest */
            change_digest: string;
            /** Command */
            command: {
                [key: string]: unknown;
            };
            /** Comment */
            comment: string | null;
            /** Decided At */
            decided_at: string | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Impact */
            impact: {
                [key: string]: unknown;
            };
            /** Kind */
            kind: string;
            /** Policy Version */
            policy_version: number;
            /** Reasons */
            reasons: string[];
            /** Reviewer Id */
            reviewer_id: string | null;
            /** Revision */
            revision: number;
            /**
             * Risk
             * @enum {string}
             */
            risk: "low" | "high";
            /**
             * Status
             * @enum {string}
             */
            status: "pending" | "approved" | "rejected";
            /**
             * Submitted At
             * Format: date-time
             */
            submitted_at: string;
            /**
             * Submitter Id
             * Format: uuid
             */
            submitter_id: string;
            /** System Policy Version */
            system_policy_version: number | "ordinary-import-v1";
            /** Target Ids */
            target_ids: string[];
        };
        /** ApprovalRef */
        ApprovalRef: {
            /** Baseline */
            baseline: {
                [key: string]: number;
            };
            /** Change Digest */
            change_digest: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Reviewer Id */
            reviewer_id: string | null;
            /**
             * Risk
             * @enum {string}
             */
            risk: "low" | "high";
            /**
             * Submitter Id
             * Format: uuid
             */
            submitter_id: string;
        };
        /** ApprovalWrite */
        ApprovalWrite: {
            /** Baseline */
            baseline: {
                [key: string]: number;
            };
            /** Change Digest */
            change_digest: string;
            /** Command */
            command: {
                [key: string]: unknown;
            };
            /** Kind */
            kind: string;
            /** Target Ids */
            target_ids: string[];
        };
        /** ArtifactDetail */
        ArtifactDetail: {
            /** Filename */
            filename: string;
            /** Links */
            links?: components["schemas"]["ArtifactLinkDetail"][];
            /** Media Type */
            media_type: string;
            /** Metadata */
            metadata: {
                [key: string]: unknown;
            };
            /** Previous Version Id */
            previous_version_id: string | null;
            /**
             * Recorded At
             * Format: date-time
             */
            recorded_at: string;
            /**
             * Resource Id
             * Format: uuid
             */
            resource_id: string;
            /** Reviews */
            reviews?: components["schemas"]["ArtifactReviewDetail"][];
            /** Revision */
            revision: number;
            /** Sha256 */
            sha256: string;
            /** Size Bytes */
            size_bytes: number;
            /**
             * Uploaded By
             * Format: uuid
             */
            uploaded_by: string;
            /**
             * Version Id
             * Format: uuid
             */
            version_id: string;
            /** Version Number */
            version_number: number;
        };
        /** ArtifactLinkDetail */
        ArtifactLinkDetail: {
            /**
             * Actor Id
             * Format: uuid
             */
            actor_id: string;
            /**
             * Event Id
             * Format: uuid
             */
            event_id: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /**
             * Recorded At
             * Format: date-time
             */
            recorded_at: string;
            /** Relation */
            relation: string;
            /**
             * Target Resource Id
             * Format: uuid
             */
            target_resource_id: string;
        };
        /** ArtifactLinkWrite */
        ArtifactLinkWrite: {
            /** Relation */
            relation: string;
            /**
             * Target Resource Id
             * Format: uuid
             */
            target_resource_id: string;
        };
        /** ArtifactMutationDetail */
        ArtifactMutationDetail: {
            /**
             * Event Id
             * Format: uuid
             */
            event_id: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Revision */
            revision: number;
            /**
             * Version Id
             * Format: uuid
             */
            version_id: string;
        };
        /** ArtifactReviewDetail */
        ArtifactReviewDetail: {
            /**
             * Actor Id
             * Format: uuid
             */
            actor_id: string;
            /** Comment */
            comment: string;
            /** Decision */
            decision: string;
            /**
             * Event Id
             * Format: uuid
             */
            event_id: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /**
             * Recorded At
             * Format: date-time
             */
            recorded_at: string;
        };
        /** ArtifactReviewWrite */
        ArtifactReviewWrite: {
            /** Comment */
            comment: string;
            /**
             * Decision
             * @enum {string}
             */
            decision: "accepted" | "rejected" | "needs_changes";
        };
        /** AssertionInput */
        AssertionInput: {
            /**
             * Assertion Id
             * Format: uuid
             */
            assertion_id: string;
            /**
             * Data Nature
             * @enum {string}
             */
            data_nature: "synthetic" | "test" | "imported_unverified" | "verified";
            object: components["schemas"]["RdfTerm"];
            /** Predicate Iri */
            predicate_iri: string;
            /**
             * Resource Id
             * Format: uuid
             */
            resource_id: string;
            /**
             * State
             * @enum {string}
             */
            state: "candidate" | "accepted";
            support: components["schemas"]["SourceSupport"];
            /**
             * Valid From
             * Format: date-time
             */
            valid_from: string;
            /** Valid To */
            valid_to?: string | null;
        };
        /** AssertionView */
        AssertionView: {
            /**
             * Bundle Id
             * Format: uuid
             */
            bundle_id: string;
            /**
             * Data Nature
             * @enum {string}
             */
            data_nature: "synthetic" | "test" | "imported_unverified" | "verified";
            decision_ref: components["schemas"]["RevisionRef"] | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            object: components["schemas"]["RdfTerm"];
            /** Predicate Iri */
            predicate_iri: string;
            /**
             * Resource Id
             * Format: uuid
             */
            resource_id: string;
            revision: components["schemas"]["RevisionRef"];
            /** State */
            state: string;
            /** Supports */
            supports: components["schemas"]["SourceSupport"][];
            /**
             * Valid From
             * Format: date-time
             */
            valid_from: string;
            /** Valid To */
            valid_to: string | null;
        };
        /** AuthorityRule */
        AuthorityRule: {
            /** Comparable Time Path */
            comparable_time_path?: string | null;
            /** Digest */
            digest: string;
            /**
             * Important
             * @default true
             */
            important: boolean;
            /**
             * Owner Policy
             * @enum {string}
             */
            owner_policy: "ontology_owned" | "source_backed" | "derived" | "read_only";
            /**
             * Owner Source Ids
             * @default []
             */
            owner_source_ids: string[];
            /** Predicate Iri */
            predicate_iri: string;
            /**
             * Source Priority
             * @default []
             */
            source_priority: string[];
        };
        /** BlobRef */
        BlobRef: {
            /** Media Type */
            media_type: string;
            /** Sha256 */
            sha256: string;
            /** Size Bytes */
            size_bytes: number;
            /**
             * Version Id
             * Format: uuid
             */
            version_id: string;
        };
        /** CandidateValue */
        CandidateValue: {
            /**
             * Bundle Id
             * Format: uuid
             */
            bundle_id: string;
            /**
             * Data Nature
             * @enum {string}
             */
            data_nature: "synthetic" | "test" | "imported_unverified" | "verified";
            /** Entity Id */
            entity_id: string | null;
            /** Evidence Refs */
            evidence_refs: components["schemas"]["RevisionRef"][];
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Locator */
            locator: string;
            normalized_value: components["schemas"]["RdfTerm"] | null;
            /** Observed At */
            observed_at: string | null;
            /** Predicate Iri */
            predicate_iri: string | null;
            /** Raw Value */
            raw_value: {
                [key: string]: unknown;
            };
            snapshot: components["schemas"]["BlobRef"];
            source_key: components["schemas"]["SourceKey"];
            /** Source Version */
            source_version: string | null;
            /** Valid From */
            valid_from: string | null;
            /** Valid To */
            valid_to: string | null;
        };
        /** ClassDef */
        ClassDef: {
            /**
             * Deprecated
             * @default false
             */
            deprecated: boolean;
            /**
             * Description
             * @default
             */
            description: string;
            /** Iri */
            iri: string;
            /** Label */
            label: string;
            /**
             * Parents
             * @default []
             */
            parents: string[];
        };
        /** ConfigurationStageCommand */
        ConfigurationStageCommand: {
            /**
             * Authority Version Id
             * Format: uuid
             */
            authority_version_id: string;
            /** Profile Ids */
            profile_ids: string[];
            /**
             * Ruling Policy Version Id
             * Format: uuid
             */
            ruling_policy_version_id: string;
        };
        /** ConflictView */
        ConflictView: {
            /** Candidate Ids */
            candidate_ids: string[];
            /** Candidate Values */
            candidate_values: components["schemas"]["CandidateValue"][];
            dependencies: components["schemas"]["DependencyBinding"];
            /** Entity Id */
            entity_id: string | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Predicate Iri */
            predicate_iri: string | null;
            /** Reason */
            reason: string;
            /** Revision */
            revision: number;
            scope: components["schemas"]["ResourceScope"];
            /**
             * State
             * @enum {string}
             */
            state: "open" | "resolved" | "needs_review" | "withdrawn";
        };
        /** ConnectionCheck */
        ConnectionCheck: {
            /**
             * Incremental Mode
             * @enum {string}
             */
            readonly incremental_mode: "timestamp_key" | "snapshot_diff";
            /** Issues */
            readonly issues: components["schemas"]["ParseIssue"][];
            /** Reachable */
            readonly reachable: boolean;
            /** Read Only */
            readonly read_only: boolean;
            /**
             * Source Id
             * Format: uuid
             */
            readonly source_id: string;
        };
        /** ConnectionConfig */
        "ConnectionConfig-Input": {
            /** Credential Ref */
            credential_ref: string;
            /** Database */
            database: string;
            /** Deleted Column */
            deleted_column?: string | null;
            /** Expected Revision */
            expected_revision: number;
            filter_expression?: components["schemas"]["RuleExpr-Input"] | null;
            /** Host */
            host: string;
            /** Key Columns */
            key_columns: string[];
            /**
             * Overlap Seconds
             * @default 60
             */
            overlap_seconds: number;
            /** Port */
            port: number;
            /** Schema Name */
            schema_name?: string | null;
            /**
             * Source Id
             * Format: uuid
             */
            source_id: string;
            /** Source Timezone */
            source_timezone?: string | null;
            /** Table Name */
            table_name: string;
            temporal_fields?: components["schemas"]["TemporalFields"] | null;
            /**
             * Tls Mode
             * @default verify
             * @enum {string}
             */
            tls_mode: "verify" | "internal_trust";
            /** Updated At Column */
            updated_at_column?: string | null;
        };
        /** ConnectionConfig */
        "ConnectionConfig-Output": {
            /** Credential Ref */
            credential_ref: string;
            /** Database */
            database: string;
            /** Deleted Column */
            deleted_column?: string | null;
            /** Expected Revision */
            expected_revision: number;
            filter_expression?: components["schemas"]["RuleExpr-Output"] | null;
            /** Host */
            host: string;
            /** Key Columns */
            key_columns: string[];
            /**
             * Overlap Seconds
             * @default 60
             */
            overlap_seconds: number;
            /** Port */
            port: number;
            /** Schema Name */
            schema_name?: string | null;
            /**
             * Source Id
             * Format: uuid
             */
            source_id: string;
            /** Source Timezone */
            source_timezone?: string | null;
            /** Table Name */
            table_name: string;
            temporal_fields?: components["schemas"]["TemporalFields"] | null;
            /**
             * Tls Mode
             * @default verify
             * @enum {string}
             */
            tls_mode: "verify" | "internal_trust";
            /** Updated At Column */
            updated_at_column?: string | null;
        };
        /** CreateDraftRequest */
        CreateDraftRequest: {
            /** Base Release Id */
            base_release_id?: string | null;
            document: components["schemas"]["SchemaDocument"];
        };
        /** CreateLinkRequest */
        CreateLinkRequest: {
            /**
             * Assertion Id
             * Format: uuid
             */
            assertion_id: string;
            /**
             * Bundle Id
             * Format: uuid
             */
            bundle_id: string;
            /**
             * Data Nature
             * @enum {string}
             */
            data_nature: "synthetic" | "test" | "imported_unverified" | "verified";
            decision_ref?: components["schemas"]["RevisionRef"] | null;
            /** Predicate Iri */
            predicate_iri: string;
            /**
             * Source Id
             * Format: uuid
             */
            source_id: string;
            /**
             * State
             * @enum {string}
             */
            state: "candidate" | "accepted";
            support: components["schemas"]["SourceSupport"];
            /**
             * Target Id
             * Format: uuid
             */
            target_id: string;
            /**
             * Valid From
             * Format: date-time
             */
            valid_from: string;
            /** Valid To */
            valid_to?: string | null;
        };
        /** CreateObjectInput */
        CreateObjectInput: {
            /**
             * Assertions
             * @default []
             */
            assertions: components["schemas"]["AssertionInput"][];
            /** Class Iris */
            class_iris: string[];
            /**
             * Data Nature
             * @enum {string}
             */
            data_nature: "synthetic" | "test" | "imported_unverified" | "verified";
            decision_ref?: components["schemas"]["RevisionRef"] | null;
            /** Display Name */
            display_name: string;
            /**
             * Owner Scope Id
             * Format: uuid
             */
            owner_scope_id: string;
            /**
             * Related Scope Ids
             * @default []
             */
            related_scope_ids: string[];
            /**
             * Resource Id
             * Format: uuid
             */
            resource_id: string;
            /**
             * State
             * @enum {string}
             */
            state: "draft" | "active";
        };
        /** CreateObjectRequest */
        CreateObjectRequest: {
            /**
             * Bundle Id
             * Format: uuid
             */
            bundle_id: string;
            create: components["schemas"]["CreateObjectInput"];
        };
        /** CredentialRef */
        CredentialRef: {
            /** Ref */
            readonly ref: string;
            /** Revision */
            readonly revision: number;
            /**
             * Source Id
             * Format: uuid
             */
            readonly source_id: string;
        };
        /** CurrentProofView */
        CurrentProofView: {
            /** Alternatives */
            alternatives: components["schemas"]["SelectedProof"][];
            /**
             * Assertion Id
             * Format: uuid
             */
            assertion_id: string;
            /**
             * Bundle Id
             * Format: uuid
             */
            bundle_id: string;
            conclusion: components["schemas"]["Triple"];
            /** Data Natures */
            data_natures: string[];
            /** Input Watermark */
            input_watermark: number;
            /** Nodes */
            nodes: components["schemas"]["SelectedProof"][];
            /** Truncated */
            truncated: boolean;
        };
        /** CurrentSchemaBaseline */
        CurrentSchemaBaseline: {
            /** Active Release Id */
            active_release_id: string | null;
        };
        /** DeactivateObjectRequest */
        DeactivateObjectRequest: {
            /** Approval Id */
            approval_id?: string | null;
            /** Reason */
            reason: string;
        };
        /** DependencyBinding */
        DependencyBinding: {
            /** Authority Rule Digest */
            authority_rule_digest: string;
            /** Digest */
            digest: string;
            /** Entity Id */
            entity_id: string | null;
            /** Evidence Digests */
            evidence_digests: string[];
            /** Predicate Iris */
            predicate_iris: string[];
            /** Ruling Rule Digest */
            ruling_rule_digest: string;
            /** Schema Dependency Digest */
            schema_dependency_digest: string;
            /** Source Keys */
            source_keys: components["schemas"]["SourceKey"][];
            /** Support Ids */
            support_ids: string[];
            /** Valid From */
            valid_from: string | null;
            /** Valid To */
            valid_to: string | null;
        };
        /** DerivedLinkView */
        DerivedLinkView: {
            /**
             * Bundle Id
             * Format: uuid
             */
            bundle_id: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Input Watermark */
            input_watermark: number;
            /** Predicate Iri */
            predicate_iri: string;
            /**
             * Proof Assertion Id
             * Format: uuid
             */
            proof_assertion_id: string;
            /**
             * Source Id
             * Format: uuid
             */
            source_id: string;
            /**
             * State
             * @default derived
             * @constant
             */
            state: "derived";
            /**
             * Target Id
             * Format: uuid
             */
            target_id: string;
        };
        /** EvidenceCreate */
        EvidenceCreate: {
            /**
             * Data Nature
             * @enum {string}
             */
            data_nature: "synthetic" | "test" | "imported_unverified" | "verified";
            /** Locator */
            locator: string | null;
            /** Replaces Id */
            replaces_id: string | null;
            scope: components["schemas"]["ResourceScope"];
            /** Source Id */
            source_id: string | null;
            /** Target Refs */
            target_refs: components["schemas"]["RevisionRef"][];
            /** Title */
            title: string;
        };
        /** EvidenceView */
        EvidenceView: {
            readonly blob: components["schemas"]["BlobRef"];
            /**
             * Created At
             * Format: date-time
             */
            readonly created_at: string;
            /**
             * Data Nature
             * @enum {string}
             */
            readonly data_nature: "synthetic" | "test" | "imported_unverified" | "verified";
            /**
             * Id
             * Format: uuid
             */
            readonly id: string;
            /** Locator */
            locator: string | null;
            /** Replaces Id */
            replaces_id: string | null;
            /**
             * Review State
             * @enum {string}
             */
            readonly review_state: "candidate" | "accepted" | "rejected";
            readonly revision: components["schemas"]["RevisionRef"];
            scope: components["schemas"]["ResourceScope"];
            /** Source Id */
            source_id: string | null;
            /** Target Refs */
            target_refs: components["schemas"]["RevisionRef"][];
            /** Title */
            title: string;
        };
        /** ExecutionBundle */
        ExecutionBundle: {
            /**
             * Authority Version Id
             * Format: uuid
             */
            authority_version_id: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /**
             * Inference Version Id
             * Format: uuid
             */
            inference_version_id: string;
            /** Mapping Version Id */
            mapping_version_id: string | null;
            /**
             * Ruling Policy Version Id
             * Format: uuid
             */
            ruling_policy_version_id: string;
            /**
             * Schema Release Id
             * Format: uuid
             */
            schema_release_id: string;
            /** Shapes Digest */
            shapes_digest: string;
        };
        /** ExportRequest */
        ExportRequest: {
            /**
             * Format
             * @enum {string}
             */
            format: "turtle" | "json-ld";
            /** Graph Role */
            graph_role?: ("schema" | "accepted" | "candidate" | "derived" | "provenance") | null;
            /**
             * Include Inferred
             * @default false
             */
            include_inferred: boolean;
            /**
             * Include Provenance
             * @default false
             */
            include_provenance: boolean;
            /**
             * Layout
             * @enum {string}
             */
            layout: "single-graph" | "dataset";
            /** Resource Ids */
            resource_ids: string[];
        };
        /** FactDifference */
        FactDifference: {
            /** After Assertion Ids */
            after_assertion_ids: string[];
            /** Before Assertion Ids */
            before_assertion_ids: string[];
            /** Expected Revision */
            expected_revision: number;
            /** Predicate Iri */
            predicate_iri: string;
            /** Requires Review */
            requires_review: boolean;
            /**
             * Resource Id
             * Format: uuid
             */
            resource_id: string;
        };
        /** FactPatch */
        FactPatch: {
            /**
             * Additions
             * @default []
             */
            additions: components["schemas"]["AssertionInput"][];
            decision_ref?: components["schemas"]["RevisionRef"] | null;
            /** Expected Revision */
            expected_revision: number;
            /**
             * Resource Id
             * Format: uuid
             */
            resource_id: string;
            /**
             * Withdraw Support Ids
             * @default []
             */
            withdraw_support_ids: string[];
        };
        /** FieldMap */
        "FieldMap-Input": {
            /** Datatype Iri */
            datatype_iri?: string | null;
            expression?: components["schemas"]["RuleExpr-Input"] | null;
            /**
             * Multiple
             * @default false
             */
            multiple: boolean;
            /** Predicate Iri */
            predicate_iri: string;
            /** Source Path */
            source_path: string;
            /** Source Unit */
            source_unit?: string | null;
            /** Target Unit */
            target_unit?: string | null;
            /**
             * Term Kind
             * @default scalar
             * @enum {string}
             */
            term_kind: "scalar" | "rdf" | "reference";
        };
        /** FieldMap */
        "FieldMap-Output": {
            /** Datatype Iri */
            datatype_iri?: string | null;
            expression?: components["schemas"]["RuleExpr-Output"] | null;
            /**
             * Multiple
             * @default false
             */
            multiple: boolean;
            /** Predicate Iri */
            predicate_iri: string;
            /** Source Path */
            source_path: string;
            /** Source Unit */
            source_unit?: string | null;
            /** Target Unit */
            target_unit?: string | null;
            /**
             * Term Kind
             * @default scalar
             * @enum {string}
             */
            term_kind: "scalar" | "rdf" | "reference";
        };
        /** FileOptions */
        FileOptions: {
            /**
             * Data Nature
             * @enum {string}
             */
            data_nature: "synthetic" | "test" | "imported_unverified" | "verified";
            /** Delimiter */
            delimiter: string;
            /** Encoding */
            encoding: string;
            /**
             * Format
             * @enum {string}
             */
            format: "csv" | "xlsx" | "json" | "geojson";
            /** Header Row */
            header_row: number;
            /** Key Paths */
            key_paths: string[];
            /** Records Path */
            records_path: string;
            /** Sheet */
            sheet: string | null;
            /** Source Crs */
            source_crs: string | null;
            /** Source Timezone */
            source_timezone: string | null;
            temporal_fields?: components["schemas"]["TemporalFields"] | null;
        };
        /** FormField */
        FormField: {
            /** Datatype Iri */
            datatype_iri: string | null;
            /** Enum Values */
            enum_values: components["schemas"]["RdfTerm"][];
            /** Iri */
            iri: string;
            /** Label */
            label: string;
            /** Max Count */
            max_count: number | null;
            /** Min Count */
            min_count: number;
            /** Read Only */
            read_only: boolean;
            /** Reference Classes */
            reference_classes: string[];
            /** Required */
            required: boolean;
            /** Unit */
            unit: string | null;
            /** Widget */
            widget: string;
        };
        /** FormSchemaView */
        FormSchemaView: {
            /** Class Iri */
            class_iri: string;
            /** Fields */
            fields: components["schemas"]["FormField"][];
            /** Inherited From */
            inherited_from: {
                [key: string]: string[];
            };
            /** Release Id */
            release_id: string | null;
        };
        /** GeoFeatureCollection */
        GeoFeatureCollection: {
            /** Features */
            features: {
                [key: string]: unknown;
            }[];
            /**
             * Gis Status
             * @default current
             * @enum {string}
             */
            gis_status: "current" | "pending" | "failed" | "unavailable";
            /** Next Cursor */
            next_cursor: string | null;
            /** Projection Watermark */
            projection_watermark: number;
            /**
             * Type
             * @default FeatureCollection
             * @constant
             */
            type: "FeatureCollection";
        };
        /** GeometryInput */
        GeometryInput: {
            decision_ref?: components["schemas"]["RevisionRef"] | null;
            /** Expected Revision */
            expected_revision: number;
            /** Geojson */
            geojson: {
                [key: string]: unknown;
            };
            /**
             * Resource Id
             * Format: uuid
             */
            resource_id: string;
            /** Source Crs */
            source_crs: string;
            source_support: components["schemas"]["SourceSupport"];
        };
        /** GeometryWriteRequest */
        GeometryWriteRequest: {
            /**
             * Bundle Id
             * Format: uuid
             */
            bundle_id: string;
            geometry: components["schemas"]["GeometryInput"];
        };
        /** GrantDetail */
        GrantDetail: {
            /** Active */
            active: boolean;
            /** Approval Id */
            approval_id?: string | null;
            /**
             * Global Scope
             * @default false
             */
            global_scope: boolean;
            /** Group Id */
            group_id?: string | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Resource Id */
            resource_id?: string | null;
            /** Revision */
            revision: number;
            /**
             * Role Id
             * Format: uuid
             */
            role_id: string;
            /** Scope Id */
            scope_id?: string | null;
            /** Security Class */
            security_class: string;
            /** User Id */
            user_id?: string | null;
        };
        /** GrantWrite */
        GrantWrite: {
            /** Approval Id */
            approval_id?: string | null;
            /**
             * Global Scope
             * @default false
             */
            global_scope: boolean;
            /** Group Id */
            group_id?: string | null;
            /** Resource Id */
            resource_id?: string | null;
            /**
             * Role Id
             * Format: uuid
             */
            role_id: string;
            /** Scope Id */
            scope_id?: string | null;
            /** Security Class */
            security_class: string;
            /** User Id */
            user_id?: string | null;
        };
        /** GraphNeighborhood */
        GraphNeighborhood: {
            /** Edges */
            edges: (components["schemas"]["LinkView"] | components["schemas"]["DerivedLinkView"])[];
            /** Input Watermark */
            input_watermark: number;
            /** Next Cursor */
            next_cursor: string | null;
            /** Nodes */
            nodes: components["schemas"]["ObjectView"][];
        };
        /** GroupDetail */
        GroupDetail: {
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Name */
            name: string;
            /** Revision */
            revision: number;
        };
        /** GroupWrite */
        GroupWrite: {
            /** Name */
            name: string;
        };
        /** HTTPValidationError */
        HTTPValidationError: {
            /** Detail */
            detail?: components["schemas"]["ValidationError"][];
        };
        /** HealthOverview */
        HealthOverview: {
            /** Expired Leases */
            expired_leases: number;
            /** Failed Jobs */
            failed_jobs: number;
            /** Failed Source Details */
            failed_source_details: components["schemas"]["SourceFailure"][];
            /** Failed Sources */
            failed_sources: number;
            /** Failed Sources Truncated */
            failed_sources_truncated: boolean;
            /** Inference Watermark */
            inference_watermark: number;
            /** Input Watermark */
            input_watermark: number;
            /** Ledger Watermark */
            ledger_watermark: number;
            /** Limits */
            limits: {
                [key: string]: number;
            };
            /** Projection Watermark */
            projection_watermark: number;
            /** Queue Depth */
            queue_depth: number;
            /** Running Jobs */
            running_jobs: number;
        };
        /** IdentityOperationDetail */
        IdentityOperationDetail: {
            /** Error Code */
            error_code: string | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Kind */
            kind: string;
            /** Phase */
            phase: string;
            /** Provider Subject */
            provider_subject: string | null;
            /** Revision */
            revision: number;
            user: components["schemas"]["UserDetail"] | null;
        };
        /** ImpactCommand */
        ImpactCommand: {
            /** Rollback Of */
            rollback_of: string | null;
            selection: components["schemas"]["ImpactSelection"];
            /**
             * Target Bundle Id
             * Format: uuid
             */
            target_bundle_id: string;
        };
        /** ImpactReport */
        ImpactReport: {
            /** Active Release Id */
            active_release_id?: string | null;
            /** Affected Ids */
            affected_ids: string[];
            /** Baseline */
            baseline: {
                [key: string]: number;
            };
            /** Digest */
            digest: string;
            /** Form Diff */
            form_diff: {
                [key: string]: unknown;
            };
            /** Migration Required */
            migration_required: boolean;
            /** Permission Diff */
            permission_diff: {
                [key: string]: unknown;
            };
            /**
             * Reasons
             * @default []
             */
            reasons: string[];
            /** Risk */
            risk?: ("low" | "high") | null;
            /** Violations */
            violations: components["schemas"]["ValidationIssue"][];
        };
        /** ImpactSelection */
        ImpactSelection: {
            /** Resource Ids */
            resource_ids: string[];
            scope: components["schemas"]["ResourceScope"];
            /** Source Ids */
            source_ids: string[];
        };
        /** ImportAdmissionPolicy */
        ImportAdmissionPolicy: {
            /** Class Iris */
            class_iris: string[];
            /**
             * Contract
             * @default ordinary-import-v1
             * @constant
             */
            contract: "ordinary-import-v1";
            /** Effect Classes */
            effect_classes: ("create" | "facts" | "replace" | "withdraw" | "geometry" | "type" | "relation")[];
            /** Max Resources */
            max_resources: number;
            /** Max Rows */
            max_rows: number;
            /** Max Withdrawals */
            max_withdrawals: number;
            /** Owner Scope Ids */
            owner_scope_ids: string[];
            /** Predicate Iris */
            predicate_iris: string[];
        };
        /** ImportPreviewCommand */
        ImportPreviewCommand: {
            /**
             * Bundle Id
             * Format: uuid
             */
            bundle_id: string;
            selection: components["schemas"]["ImportSelection"];
        };
        /** ImportRunView */
        ImportRunView: {
            /** Applied Count */
            readonly applied_count: number;
            /**
             * Bundle Id
             * Format: uuid
             */
            readonly bundle_id: string;
            /** Complete */
            readonly complete: boolean;
            /** Failed Count */
            readonly failed_count: number;
            /**
             * Id
             * Format: uuid
             */
            readonly id: string;
            /** Inference Watermark */
            readonly inference_watermark: number | null;
            readonly job: components["schemas"]["JobRef"];
            /**
             * Preview Id
             * Format: uuid
             */
            readonly preview_id: string;
            /**
             * Projection State
             * @enum {string}
             */
            readonly projection_state: "pending" | "synced" | "failed";
            /** Projection Watermark */
            readonly projection_watermark: number | null;
            /** Read Count */
            readonly read_count: number;
            /** Review Count */
            readonly review_count: number;
            /** Schema Draft Id */
            readonly schema_draft_id: string | null;
        };
        /** ImportSelection */
        ImportSelection: {
            /** Base Release Id */
            base_release_id?: string | null;
            /** File Options */
            file_options?: components["schemas"]["FileOptions"] | components["schemas"]["SemanticFileOptions"] | null;
            /**
             * Mode
             * @enum {string}
             */
            mode: "full" | "incremental";
            /** Scan Id */
            scan_id?: string | null;
            /** Semantic Format */
            semantic_format?: ("turtle" | "json-ld") | null;
            /** Semantic Kind */
            semantic_kind?: ("schema" | "shapes" | "instances") | null;
            /**
             * Source Id
             * Format: uuid
             */
            source_id: string;
            /** Target Draft Id */
            target_draft_id?: string | null;
            /** Target Draft Revision */
            target_draft_revision?: number | null;
            upload?: components["schemas"]["BlobRef"] | null;
        };
        /** InheritanceView */
        InheritanceView: {
            /** Ancestors */
            ancestors: string[];
            /** Class Iri */
            class_iri: string;
            /** Conflicts */
            conflicts: string[];
            /** Fields */
            fields: components["schemas"]["FormField"][];
        };
        /** InvitationWrite */
        InvitationWrite: {
            /** Display Name */
            display_name: string;
            /** Email */
            email: string;
        };
        /** JobRef */
        JobRef: {
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /**
             * Status
             * @enum {string}
             */
            status: "queued" | "running" | "paused" | "failed" | "succeeded" | "cancelled";
        };
        /** JobResultRef */
        JobResultRef: {
            /** Api Path */
            api_path: string;
            /** Kind */
            kind: string;
            /**
             * Resource Id
             * Format: uuid
             */
            resource_id: string;
        };
        /** JobView */
        JobView: {
            /** Error Code */
            error_code?: string | null;
            job: components["schemas"]["JobRef"];
            /** Progress */
            progress: {
                [key: string]: unknown;
            };
            result_ref?: components["schemas"]["JobResultRef"] | null;
            /** Revision */
            revision: number;
        };
        /** LinkView */
        LinkView: {
            assertion: components["schemas"]["AssertionView"];
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Predicate Iri */
            predicate_iri: string;
            /**
             * Source Id
             * Format: uuid
             */
            source_id: string;
            /**
             * Target Id
             * Format: uuid
             */
            target_id: string;
        };
        /** LocationObservationInput */
        LocationObservationInput: {
            /** Expected Revision */
            expected_revision: number;
            /**
             * Observation Id
             * Format: uuid
             */
            observation_id: string;
            position: components["schemas"]["PositionInput"];
            /**
             * Resource Id
             * Format: uuid
             */
            resource_id: string;
            /** Source */
            source: string;
        };
        /** LocationObservationRequest */
        LocationObservationRequest: {
            /**
             * Bundle Id
             * Format: uuid
             */
            bundle_id: string;
            observation: components["schemas"]["LocationObservationInput"];
        };
        /** LogoutDetail */
        LogoutDetail: {
            /** Logged Out */
            logged_out: boolean;
            /** Provider Logged Out */
            provider_logged_out: boolean;
        };
        /** MapResourceCreate */
        MapResourceCreate: {
            /** Artifact Version Id */
            artifact_version_id?: string | null;
            /** Asset Versions */
            asset_versions?: {
                [key: string]: string;
            };
            /** Attribution */
            attribution: string;
            /** Bbox */
            bbox: [
                number,
                number,
                number,
                number
            ];
            /**
             * Kind
             * @enum {string}
             */
            kind: "online" | "mbtiles" | "pmtiles";
            /** License */
            license: string;
            /** Max Zoom */
            max_zoom: number;
            /** Min Zoom */
            min_zoom: number;
            /** Online Url Template */
            online_url_template?: string | null;
            /**
             * Owner Scope Id
             * Format: uuid
             */
            owner_scope_id: string;
            /** Style Version Id */
            style_version_id?: string | null;
            /**
             * Tile Kind
             * @enum {string}
             */
            tile_kind: "raster" | "mvt";
            /** Title */
            title: string;
        };
        /** MapResourcePage */
        MapResourcePage: {
            /** Items */
            items: components["schemas"]["MapResourceView"][];
            /** Next Cursor */
            next_cursor: string | null;
        };
        /** MapResourcePatch */
        MapResourcePatch: {
            /** Artifact Version Id */
            artifact_version_id?: string | null;
            /** Asset Versions */
            asset_versions?: {
                [key: string]: string;
            } | null;
            /** Attribution */
            attribution?: string | null;
            /** Bbox */
            bbox?: [
                number,
                number,
                number,
                number
            ] | null;
            /** Kind */
            kind?: ("online" | "mbtiles" | "pmtiles") | null;
            /** License */
            license?: string | null;
            /** Max Zoom */
            max_zoom?: number | null;
            /** Min Zoom */
            min_zoom?: number | null;
            /** Online Url Template */
            online_url_template?: string | null;
            /** Owner Scope Id */
            owner_scope_id?: string | null;
            /** Style Version Id */
            style_version_id?: string | null;
            /** Tile Kind */
            tile_kind?: ("raster" | "mvt") | null;
            /** Title */
            title?: string | null;
        };
        /** MapResourceView */
        MapResourceView: {
            /** Artifact Version Id */
            artifact_version_id?: string | null;
            /** Asset Versions */
            asset_versions?: {
                [key: string]: string;
            };
            /** Attribution */
            attribution: string;
            /** Bbox */
            bbox: [
                number,
                number,
                number,
                number
            ];
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /**
             * Kind
             * @enum {string}
             */
            kind: "online" | "mbtiles" | "pmtiles";
            /** License */
            license: string;
            /** Max Zoom */
            max_zoom: number;
            /** Min Zoom */
            min_zoom: number;
            /** Online Url Template */
            online_url_template?: string | null;
            /**
             * Owner Scope Id
             * Format: uuid
             */
            owner_scope_id: string;
            /** Revision */
            revision: number;
            /** Style Version Id */
            style_version_id?: string | null;
            /**
             * Tile Kind
             * @enum {string}
             */
            tile_kind: "raster" | "mvt";
            /** Title */
            title: string;
            /**
             * Validation Status
             * @constant
             */
            validation_status: "valid";
        };
        /** MappingProfile */
        "MappingProfile-Input": {
            /** Class Iri */
            class_iri: string;
            /** Confirmed Identity Rule Id */
            confirmed_identity_rule_id: string | null;
            /**
             * Digest
             * @default
             */
            readonly digest: string;
            /** Fields */
            fields: components["schemas"]["FieldMap-Input"][];
            /** Geometry Path */
            geometry_path: string | null;
            /**
             * Id
             * Format: uuid
             */
            readonly id?: string;
            /** Identity Paths */
            identity_paths: string[];
            /** Name Path */
            name_path: string;
            /**
             * Revision
             * @default 1
             */
            readonly revision: number;
            /** Source Crs */
            source_crs: string | null;
            /**
             * Source Id
             * Format: uuid
             */
            source_id: string;
            /** Source Timezone */
            source_timezone: string | null;
        };
        /** MappingProfile */
        "MappingProfile-Output": {
            /** Class Iri */
            class_iri: string;
            /** Confirmed Identity Rule Id */
            confirmed_identity_rule_id: string | null;
            /**
             * Digest
             * @default
             */
            readonly digest: string;
            /** Fields */
            fields: components["schemas"]["FieldMap-Output"][];
            /** Geometry Path */
            geometry_path: string | null;
            /**
             * Id
             * Format: uuid
             */
            readonly id?: string;
            /** Identity Paths */
            identity_paths: string[];
            /** Name Path */
            name_path: string;
            /**
             * Revision
             * @default 1
             */
            readonly revision: number;
            /** Source Crs */
            source_crs: string | null;
            /**
             * Source Id
             * Format: uuid
             */
            source_id: string;
            /** Source Timezone */
            source_timezone: string | null;
        };
        /** MembersDetail */
        MembersDetail: {
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Revision */
            revision: number;
            /** User Ids */
            user_ids: string[];
        };
        /** MembersWrite */
        MembersWrite: {
            /** Approval Id */
            approval_id?: string | null;
            /** User Ids */
            user_ids: string[];
        };
        /** MergeCommand */
        MergeCommand: {
            /**
             * Approval Id
             * Format: uuid
             */
            approval_id: string;
            /**
             * Proposal Id
             * Format: uuid
             */
            proposal_id: string;
        };
        /** MergePreviewCommand */
        MergePreviewCommand: {
            /**
             * Canonical Id
             * Format: uuid
             */
            canonical_id: string;
            /** Duplicate Ids */
            duplicate_ids: string[];
        };
        /** MergeProposal */
        MergeProposal: {
            /**
             * Approval Id
             * Format: uuid
             */
            approval_id: string;
            /** Baseline */
            baseline: {
                [key: string]: number;
            };
            /**
             * Blocking Reasons
             * @default []
             */
            readonly blocking_reasons: string[];
            /**
             * Canonical Id
             * Format: uuid
             */
            canonical_id: string;
            /** Change Digest */
            change_digest: string;
            /** Conflict Ids */
            conflict_ids: string[];
            /**
             * Different Manager Ids
             * @default []
             */
            readonly different_manager_ids: string[];
            /** Duplicate Ids */
            duplicate_ids: string[];
            /** Evidence Refs */
            evidence_refs: components["schemas"]["RevisionRef"][];
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /**
             * Preserved Candidate Assertion Ids
             * @default []
             */
            readonly preserved_candidate_assertion_ids: string[];
            /** Revision */
            revision: number;
            /** Scopes */
            scopes: components["schemas"]["ResourceScope"][];
        };
        /** ObjectView */
        ObjectView: {
            /**
             * Bundle Id
             * Format: uuid
             */
            bundle_id: string;
            candidate_validation_report: components["schemas"]["ValidationReport"] | null;
            /** Class Iris */
            class_iris: string[];
            /**
             * Data Nature
             * @enum {string}
             */
            data_nature: "synthetic" | "test" | "imported_unverified" | "verified";
            /** Display Name */
            display_name: string;
            /**
             * Evaluation Time
             * Format: date-time
             */
            evaluation_time: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Iri */
            iri: string;
            /** Next Transition At */
            next_transition_at: string | null;
            /**
             * Owner Scope Id
             * Format: uuid
             */
            owner_scope_id: string;
            /** Projection State */
            projection_state: string;
            /** Properties */
            properties: {
                [key: string]: components["schemas"]["RdfTerm"][];
            };
            /** Related Scope Ids */
            related_scope_ids: string[];
            /** Revision */
            revision: number;
            /** State */
            state: string;
            validation_report: components["schemas"]["ValidationReport"] | null;
            /**
             * Validation State
             * @enum {string}
             */
            validation_state: "conforms" | "needs_review" | "pending";
        };
        /** Page */
        Page_ApprovalDetail_: {
            /** Items */
            items: components["schemas"]["ApprovalDetail"][];
            /** Next Cursor */
            next_cursor: string | null;
        };
        /** Page */
        Page_AssertionView_: {
            /** Items */
            items: components["schemas"]["AssertionView"][];
            /** Next Cursor */
            next_cursor: string | null;
        };
        /** Page */
        Page_ConflictView_: {
            /** Items */
            items: components["schemas"]["ConflictView"][];
            /** Next Cursor */
            next_cursor: string | null;
        };
        /** Page */
        Page_GrantDetail_: {
            /** Items */
            items: components["schemas"]["GrantDetail"][];
            /** Next Cursor */
            next_cursor: string | null;
        };
        /** Page */
        Page_GroupDetail_: {
            /** Items */
            items: components["schemas"]["GroupDetail"][];
            /** Next Cursor */
            next_cursor: string | null;
        };
        /** Page */
        Page_IdentityOperationDetail_: {
            /** Items */
            items: components["schemas"]["IdentityOperationDetail"][];
            /** Next Cursor */
            next_cursor: string | null;
        };
        /** Page */
        Page_JobView_: {
            /** Items */
            items: components["schemas"]["JobView"][];
            /** Next Cursor */
            next_cursor: string | null;
        };
        /** Page */
        Page_LinkView_: {
            /** Items */
            items: components["schemas"]["LinkView"][];
            /** Next Cursor */
            next_cursor: string | null;
        };
        /** Page */
        Page_ObjectView_: {
            /** Items */
            items: components["schemas"]["ObjectView"][];
            /** Next Cursor */
            next_cursor: string | null;
        };
        /** Page */
        Page_RevisionRef_: {
            /** Items */
            items: components["schemas"]["RevisionRef"][];
            /** Next Cursor */
            next_cursor: string | null;
        };
        /** Page */
        Page_RoleDetail_: {
            /** Items */
            items: components["schemas"]["RoleDetail"][];
            /** Next Cursor */
            next_cursor: string | null;
        };
        /** Page */
        Page_RowResult_: {
            /** Items */
            items: components["schemas"]["RowResult"][];
            /** Next Cursor */
            next_cursor: string | null;
        };
        /** Page */
        Page_RuleVersion_: {
            /** Items */
            items: components["schemas"]["RuleVersion-Output"][];
            /** Next Cursor */
            next_cursor: string | null;
        };
        /** Page */
        Page_RulingView_: {
            /** Items */
            items: components["schemas"]["RulingView"][];
            /** Next Cursor */
            next_cursor: string | null;
        };
        /** Page */
        Page_SchemaDraftView_: {
            /** Items */
            items: components["schemas"]["SchemaDraftView"][];
            /** Next Cursor */
            next_cursor: string | null;
        };
        /** Page */
        Page_SchemaReleaseView_: {
            /** Items */
            items: components["schemas"]["SchemaReleaseView"][];
            /** Next Cursor */
            next_cursor: string | null;
        };
        /** Page */
        Page_SourceView_: {
            /** Items */
            items: components["schemas"]["SourceView"][];
            /** Next Cursor */
            next_cursor: string | null;
        };
        /** Page */
        Page_UserDetail_: {
            /** Items */
            items: components["schemas"]["UserDetail"][];
            /** Next Cursor */
            next_cursor: string | null;
        };
        /** ParseIssue */
        ParseIssue: {
            /** Code */
            code: string;
            /** Field Path */
            field_path: string | null;
            /** Locator */
            locator: string;
            /** Message */
            message: string;
        };
        /** PermissionSummary */
        PermissionSummary: {
            /** Actions */
            actions: string[];
            /** Global Scope */
            global_scope: boolean;
            /** Resource Id */
            resource_id: string | null;
            /**
             * Role Id
             * Format: uuid
             */
            role_id: string;
            /** Scope Id */
            scope_id: string | null;
            /** Security Class */
            security_class: string;
        };
        /** PositionInput */
        PositionInput: {
            /** Accuracy Metres */
            accuracy_metres?: number | null;
            /** Height Metres */
            height_metres?: number | null;
            /**
             * Horizontal Datum
             * @enum {string}
             */
            horizontal_datum: "EPSG:4326" | "EPSG:4490";
            /** Latitude */
            latitude: number;
            /** Longitude */
            longitude: number;
            /**
             * Observed At
             * Format: date-time
             */
            observed_at: string;
            /** Vertical Datum */
            vertical_datum?: string | null;
        };
        /** PreviewDetail */
        PreviewDetail: {
            /** Baseline */
            baseline: {
                [key: string]: number;
            };
            /** Change Digest */
            change_digest: string;
            /** Command */
            command: {
                [key: string]: unknown;
            };
            /** Impact */
            impact: {
                [key: string]: unknown;
            };
            /** Kind */
            kind: string;
            /** Reasons */
            reasons: string[];
            /** Risk */
            risk: string;
            /** Target Ids */
            target_ids: string[];
        };
        /** PreviewReport */
        PreviewReport: {
            /** Approval Id */
            readonly approval_id: string | null;
            /** Baseline */
            readonly baseline: {
                [key: string]: number;
            };
            readonly bundle: components["schemas"]["ExecutionBundle"];
            /** Change Digest */
            readonly change_digest: string;
            /** Complete */
            readonly complete: boolean;
            /**
             * Id
             * Format: uuid
             */
            readonly id: string;
            /** Input Digest */
            readonly input_digest: string;
            /** Invalid Count */
            readonly invalid_count: number;
            /** Ready Count */
            readonly ready_count: number;
            /** Review Count */
            readonly review_count: number;
            /** Revision */
            readonly revision: number;
            /** Rows */
            readonly rows: components["schemas"]["RowResult"][];
            readonly selection: components["schemas"]["ImportSelection"];
        };
        /** PreviewWrite */
        PreviewWrite: {
            /** Command */
            command: {
                [key: string]: unknown;
            };
            /** Kind */
            kind: string;
            /** Target Ids */
            target_ids: string[];
        };
        /** PropertyDef */
        PropertyDef: {
            /** Datatype Iri */
            datatype_iri: string;
            /**
             * Enum Values
             * @default []
             */
            enum_values: components["schemas"]["RdfTerm"][];
            /** Iri */
            iri: string;
            /** Label */
            label: string;
            /** Owner Policy Iri */
            owner_policy_iri: string;
            /**
             * Superproperties
             * @default []
             */
            superproperties: string[];
            /** Unit */
            unit?: string | null;
        };
        /** PublishReleaseRequest */
        PublishReleaseRequest: {
            /**
             * Approval Id
             * Format: uuid
             */
            approval_id: string;
            /**
             * Draft Id
             * Format: uuid
             */
            draft_id: string;
        };
        /** PublishRuleCommand */
        PublishRuleCommand: {
            /**
             * Approval Id
             * Format: uuid
             */
            approval_id: string;
            /** Expected Revision */
            expected_revision: number;
            /**
             * Impact Id
             * Format: uuid
             */
            impact_id: string;
            /**
             * Rule Version Id
             * Format: uuid
             */
            rule_version_id: string;
        };
        /** QueryBlankNode */
        QueryBlankNode: {
            /**
             * Kind
             * @constant
             */
            kind: "bnode";
            /** Value */
            value: string;
        };
        /** QueryRequest */
        QueryRequest: {
            /**
             * Max Rows
             * @default 200
             */
            max_rows: number;
            /**
             * Mode
             * @default current
             * @enum {string}
             */
            mode: "current" | "review";
            /** Query */
            query: string;
            /**
             * Timeout Ms
             * @default 10000
             */
            timeout_ms: number;
        };
        /** QueryResult */
        QueryResult: {
            /** Authorization Version */
            authorization_version: number;
            /** Boolean */
            boolean: boolean | null;
            /** Graph */
            graph: components["schemas"]["QueryTriple"][];
            /** Input Watermark */
            input_watermark: number;
            /**
             * Kind
             * @enum {string}
             */
            kind: "select" | "ask" | "graph";
            /** Rows */
            rows: {
                [key: string]: components["schemas"]["RdfTerm"] | components["schemas"]["QueryBlankNode"];
            }[];
            /** Truncated */
            truncated: boolean;
            /** Variables */
            variables: string[];
        };
        /** QueryTriple */
        QueryTriple: {
            /** Object */
            object: components["schemas"]["RdfTerm"] | components["schemas"]["QueryBlankNode"];
            /** Predicate Iri */
            predicate_iri: string;
            subject_bnode?: components["schemas"]["QueryBlankNode"] | null;
            /** Subject Iri */
            subject_iri?: string | null;
        };
        /** RdfTerm */
        RdfTerm: {
            /** Datatype Iri */
            datatype_iri?: string | null;
            /**
             * Kind
             * @enum {string}
             */
            kind: "iri" | "literal";
            /** Language */
            language?: string | null;
            /** Value */
            value: string;
        };
        /** RefreshConflictCommand */
        RefreshConflictCommand: {
            /** Expected Revision */
            expected_revision: number;
            ruling_ref: components["schemas"]["RevisionRef"];
        };
        /** RelationDef */
        RelationDef: {
            /**
             * Domain Iris
             * @default []
             */
            domain_iris: string[];
            /** Inverse Iri */
            inverse_iri?: string | null;
            /** Iri */
            iri: string;
            /** Label */
            label: string;
            /**
             * Range Iris
             * @default []
             */
            range_iris: string[];
            /**
             * Superproperties
             * @default []
             */
            superproperties: string[];
            /**
             * Symmetric
             * @default false
             */
            symmetric: boolean;
            /**
             * Transitive
             * @default false
             */
            transitive: boolean;
        };
        /** ResourceScope */
        ResourceScope: {
            /** Owner Scope Id */
            owner_scope_id: string | null;
            /** Related Scope Ids */
            related_scope_ids: string[];
            /** Resource Id */
            resource_id: string | null;
            /** Security Class */
            security_class: string;
        };
        /** ReviewRulingCommand */
        ReviewRulingCommand: {
            /** Expected Revision */
            expected_revision: number;
            ruling_ref: components["schemas"]["RevisionRef"];
        };
        /** RevisionDetail */
        RevisionDetail: {
            /**
             * Actor Id
             * Format: uuid
             */
            actor_id: string;
            /** Bundle Id */
            bundle_id: string | null;
            /** Effective At */
            effective_at: string | null;
            /** Evidence Refs */
            evidence_refs: string[];
            /** Payload */
            payload: {
                [key: string]: unknown;
            };
            /** Proof Refs */
            proof_refs: string[];
            /**
             * Recorded At
             * Format: date-time
             */
            recorded_at: string;
            ref: components["schemas"]["RevisionRef"];
            /** Task Id */
            task_id: string | null;
        };
        /** RevisionDiff */
        RevisionDiff: {
            /** Actors */
            actors: string[];
            /** Added */
            added: {
                [key: string]: unknown;
            };
            /** Changed */
            changed: {
                [key: string]: unknown;
            };
            /** Event Ids */
            event_ids: string[];
            /** From Revision */
            from_revision: number;
            /** Removed */
            removed: {
                [key: string]: unknown;
            };
            /**
             * Resource Id
             * Format: uuid
             */
            resource_id: string;
            /** To Revision */
            to_revision: number;
        };
        /** RevisionRef */
        RevisionRef: {
            /**
             * Event Id
             * Format: uuid
             */
            event_id: string;
            /**
             * Resource Id
             * Format: uuid
             */
            resource_id: string;
            /** Revision */
            revision: number;
        };
        /** RoleDetail */
        RoleDetail: {
            /** Actions */
            actions: string[];
            /** Approval Id */
            approval_id?: string | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Name */
            name: string;
            /** Revision */
            revision: number;
        };
        /** RoleWrite */
        RoleWrite: {
            /** Actions */
            actions: string[];
            /** Approval Id */
            approval_id?: string | null;
            /** Name */
            name: string;
        };
        /** RowResult */
        RowResult: {
            /**
             * Assertion Ids
             * @default []
             */
            readonly assertion_ids: string[];
            /**
             * Conflict Ids
             * @default []
             */
            readonly conflict_ids: string[];
            /** Entity Id */
            readonly entity_id?: string | null;
            /**
             * Issue Refs
             * @default []
             */
            readonly issue_refs: components["schemas"]["ParseIssue"][];
            /** Locator */
            readonly locator: string;
            readonly revision?: components["schemas"]["RevisionRef"] | null;
            readonly source_key: components["schemas"]["SourceKey"];
            /**
             * State
             * @enum {string}
             */
            readonly state: "ready" | "needs_review" | "invalid" | "applied" | "failed" | "unchanged";
        };
        /** RuleExpr */
        "RuleExpr-Input": {
            /**
             * Args
             * @default []
             */
            args: components["schemas"]["RuleExpr-Input"][];
            /**
             * Op
             * @enum {string}
             */
            op: "path" | "literal" | "coalesce" | "cast" | "unit" | "date" | "normalize" | "eq" | "and" | "or";
            /** Path */
            path?: string | null;
            /** Value */
            value?: {
                [key: string]: unknown;
            } | null;
        };
        /** RuleExpr */
        "RuleExpr-Output": {
            /**
             * Args
             * @default []
             */
            args: components["schemas"]["RuleExpr-Output"][];
            /**
             * Op
             * @enum {string}
             */
            op: "path" | "literal" | "coalesce" | "cast" | "unit" | "date" | "normalize" | "eq" | "and" | "or";
            /** Path */
            path?: string | null;
            /** Value */
            value?: {
                [key: string]: unknown;
            } | null;
        };
        /** RuleImpact */
        RuleImpact: {
            /**
             * Approval Id
             * Format: uuid
             */
            approval_id: string;
            /** Baseline */
            baseline: {
                [key: string]: number;
            };
            /** Change Digest */
            change_digest: string;
            /** Differences */
            differences: components["schemas"]["FactDifference"][];
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Input Snapshots */
            input_snapshots: components["schemas"]["BlobRef"][];
            /** Input Watermark */
            input_watermark: number;
            /** New Conflict Ids */
            new_conflict_ids: string[];
            /** Preserved Ruling Ids */
            preserved_ruling_ids: string[];
            /** Reevaluate Ruling Ids */
            reevaluate_ruling_ids: string[];
            /** Revision */
            revision: number;
            /** Rollback Of */
            rollback_of: string | null;
            selection: components["schemas"]["ImpactSelection"];
            target_bundle: components["schemas"]["ExecutionBundle"];
        };
        /** RuleVersion */
        "RuleVersion-Input": {
            /** Alignment Rules */
            alignment_rules: components["schemas"]["AlignmentRule-Input"][];
            /** Authority Rules */
            authority_rules: components["schemas"]["AuthorityRule"][];
            /**
             * Digest
             * @default
             */
            readonly digest: string;
            /**
             * Id
             * Format: uuid
             */
            readonly id?: string;
            import_admission?: components["schemas"]["ImportAdmissionPolicy"] | null;
            /**
             * Kind
             * @enum {string}
             */
            kind: "mapping" | "alignment" | "authority" | "ruling";
            mapping_profile: components["schemas"]["MappingProfile-Input"] | null;
            /** Parent Id */
            parent_id: string | null;
            /**
             * Revision
             * @default 1
             */
            readonly revision: number;
            /** Rules */
            rules: components["schemas"]["RuleExpr-Input"][];
            /** Ruling Policies */
            ruling_policies: components["schemas"]["RulingPolicyRule-Input"][];
            scope: components["schemas"]["ResourceScope"];
            /**
             * Status
             * @default draft
             * @enum {string}
             */
            readonly status: "draft" | "approved" | "published" | "deprecated";
        };
        /** RuleVersion */
        "RuleVersion-Output": {
            /** Alignment Rules */
            alignment_rules: components["schemas"]["AlignmentRule-Output"][];
            /** Authority Rules */
            authority_rules: components["schemas"]["AuthorityRule"][];
            /**
             * Digest
             * @default
             */
            readonly digest: string;
            /**
             * Id
             * Format: uuid
             */
            readonly id?: string;
            import_admission?: components["schemas"]["ImportAdmissionPolicy"] | null;
            /**
             * Kind
             * @enum {string}
             */
            kind: "mapping" | "alignment" | "authority" | "ruling";
            mapping_profile: components["schemas"]["MappingProfile-Output"] | null;
            /** Parent Id */
            parent_id: string | null;
            /**
             * Revision
             * @default 1
             */
            readonly revision: number;
            /** Rules */
            rules: components["schemas"]["RuleExpr-Output"][];
            /** Ruling Policies */
            ruling_policies: components["schemas"]["RulingPolicyRule-Output"][];
            scope: components["schemas"]["ResourceScope"];
            /**
             * Status
             * @default draft
             * @enum {string}
             */
            readonly status: "draft" | "approved" | "published" | "deprecated";
        };
        /** RulingCommand */
        RulingCommand: {
            /**
             * Approval Id
             * Format: uuid
             */
            approval_id: string;
            /** Chosen Candidate Ids */
            chosen_candidate_ids: string[];
            /** Expected Revision */
            expected_revision: number;
            /** Rationale */
            rationale: string;
            /** Rejected Candidate Ids */
            rejected_candidate_ids: string[];
        };
        /** RulingPolicyRule */
        "RulingPolicyRule-Input": {
            /** Always Review */
            always_review: boolean;
            /** Digest */
            digest: string;
            expression: components["schemas"]["RuleExpr-Input"];
            /** Predicate Iris */
            predicate_iris: string[];
        };
        /** RulingPolicyRule */
        "RulingPolicyRule-Output": {
            /** Always Review */
            always_review: boolean;
            /** Digest */
            digest: string;
            expression: components["schemas"]["RuleExpr-Output"];
            /** Predicate Iris */
            predicate_iris: string[];
        };
        /** RulingView */
        RulingView: {
            /** Chosen Candidate Ids */
            chosen_candidate_ids: string[];
            /**
             * Conflict Id
             * Format: uuid
             */
            conflict_id: string;
            /**
             * Decided At
             * Format: date-time
             */
            decided_at: string;
            dependencies: components["schemas"]["DependencyBinding"];
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Rationale */
            rationale: string;
            /** Rejected Candidate Ids */
            rejected_candidate_ids: string[];
            /** Replaces Id */
            replaces_id: string | null;
            /**
             * Reviewer Id
             * Format: uuid
             */
            reviewer_id: string;
            revision: components["schemas"]["RevisionRef"];
            /**
             * State
             * @enum {string}
             */
            state: "effective" | "needs_review" | "superseded";
        };
        /** ScheduleCommand */
        ScheduleCommand: {
            /** Enabled */
            enabled: boolean;
            /** Expected Revision */
            expected_revision: number;
            /** Interval Seconds */
            interval_seconds: number;
            /**
             * Source Id
             * Format: uuid
             */
            source_id: string;
        };
        /** ScheduleView */
        ScheduleView: {
            /** Enabled */
            enabled: boolean;
            /** Interval Seconds */
            interval_seconds: number;
            /** Next Run At */
            readonly next_run_at: string | null;
            /** Revision */
            readonly revision: number;
            /**
             * Source Id
             * Format: uuid
             */
            readonly source_id: string;
        };
        /** SchemaDiffView */
        SchemaDiffView: {
            /**
             * Base Release Id
             * Format: uuid
             */
            base_release_id: string;
            /** Changes */
            changes: {
                [key: string]: unknown;
            };
            impact: components["schemas"]["ImpactReport"];
            /**
             * Target Release Id
             * Format: uuid
             */
            target_release_id: string;
        };
        /** SchemaDocument */
        SchemaDocument: {
            /**
             * Classes
             * @default []
             */
            classes: components["schemas"]["ClassDef"][];
            /**
             * Dependencies
             * @default []
             */
            dependencies: components["schemas"]["BlobRef"][];
            /**
             * Properties
             * @default []
             */
            properties: components["schemas"]["PropertyDef"][];
            /**
             * Relations
             * @default []
             */
            relations: components["schemas"]["RelationDef"][];
            /**
             * Shapes
             * @default
             */
            shapes: string;
            /** Ui Metadata */
            ui_metadata?: {
                [key: string]: unknown;
            };
        };
        /** SchemaDraftView */
        SchemaDraftView: {
            /** Base Release Id */
            base_release_id: string | null;
            current_baseline?: components["schemas"]["CurrentSchemaBaseline"] | null;
            document: components["schemas"]["SchemaDocument"];
            /** Document Digest */
            document_digest?: string | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Revision */
            revision: number;
            /** State */
            state: string;
        };
        /** SchemaReleaseView */
        SchemaReleaseView: {
            /** Artifacts */
            artifacts: components["schemas"]["BlobRef"][];
            document: components["schemas"]["SchemaDocument"];
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Revision */
            revision: number;
            /** State */
            state: string;
            /** Version */
            version: string;
        };
        /** SelectedPremise */
        SelectedPremise: {
            /** Assertion Id */
            assertion_id?: string | null;
            conclusion: components["schemas"]["Triple"];
            /**
             * Kind
             * @enum {string}
             */
            kind: "assertion" | "type" | "proof" | "axiom";
            /** Proof Id */
            proof_id?: string | null;
            /** Schema Axiom Iri */
            schema_axiom_iri?: string | null;
            type_ref?: components["schemas"]["TypePremiseRef"] | null;
        };
        /**
         * SelectedProof
         * @description Displayed witnesses for a stored proof; canonical edges are unchanged.
         */
        SelectedProof: {
            conclusion: components["schemas"]["Triple"];
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Rule Iri */
            rule_iri: string;
            /** Witnesses */
            witnesses: components["schemas"]["SelectedPremise"][];
        };
        /** SemanticDraftImportBody */
        SemanticDraftImportBody: {
            /** Base Release Id */
            base_release_id?: string | null;
            blob: components["schemas"]["BlobRef"];
            /**
             * Format
             * @enum {string}
             */
            format: "turtle" | "json-ld";
            /**
             * Kind
             * @enum {string}
             */
            kind: "schema" | "shapes";
            /** Target Draft Id */
            target_draft_id?: string | null;
        };
        /** SemanticFileOptions */
        SemanticFileOptions: {
            /**
             * Data Nature
             * @enum {string}
             */
            data_nature: "synthetic" | "test" | "imported_unverified" | "verified";
            /**
             * Format
             * @enum {string}
             */
            format: "turtle" | "json-ld";
            /** Source Timezone */
            source_timezone?: string | null;
            temporal_fields?: components["schemas"]["TemporalFields"] | null;
        };
        /** SessionDetail */
        SessionDetail: {
            /** Authorization Version */
            authorization_version: number;
            /** Csrf Token */
            csrf_token: string;
            /** Permissions */
            permissions: components["schemas"]["PermissionSummary"][];
            user: components["schemas"]["UserDetail"];
        };
        /** SourceCreate */
        SourceCreate: {
            /** Collection */
            collection: string;
            /**
             * Data Nature
             * @enum {string}
             */
            data_nature: "synthetic" | "test" | "imported_unverified" | "verified";
            /**
             * Kind
             * @enum {string}
             */
            kind: "file" | "postgresql" | "mysql";
            scope: components["schemas"]["ResourceScope"];
            /** Source Instance */
            source_instance: string;
            /** Source System */
            source_system: string;
        };
        /** SourceCredentialInput */
        SourceCredentialInput: {
            /** Expected Revision */
            expected_revision: number;
            /**
             * Password
             * Format: password
             */
            password: string;
            /** Username */
            username: string;
        };
        /** SourceFailure */
        SourceFailure: {
            /** Error Code */
            error_code: string | null;
            /**
             * Source Id
             * Format: uuid
             */
            source_id: string;
            /** Status */
            status: string;
        };
        /** SourceKey */
        SourceKey: {
            /** Canonical Key */
            canonical_key: string;
            /** Collection */
            collection: string;
            /**
             * Source Id
             * Format: uuid
             */
            source_id: string;
            /** Source Instance */
            source_instance: string;
            /** Source System */
            source_system: string;
        };
        /** SourceSupport */
        SourceSupport: {
            /**
             * Evidence Refs
             * @default []
             */
            evidence_refs: components["schemas"]["RevisionRef"][];
            /** Mapping Version Id */
            mapping_version_id?: string | null;
            /** Observed At */
            observed_at?: string | null;
            /** Raw Value */
            raw_value: {
                [key: string]: unknown;
            };
            /** Record Key */
            record_key?: string | null;
            snapshot?: components["schemas"]["BlobRef"] | null;
            /** Source Id */
            source_id?: string | null;
            /** Source Version */
            source_version?: string | null;
            /**
             * Support Id
             * Format: uuid
             */
            support_id: string;
        };
        /** SourceView */
        SourceView: {
            configuration: components["schemas"]["SourceCreate"];
            /** Connection Revision */
            readonly connection_revision?: number | null;
            /**
             * Id
             * Format: uuid
             */
            readonly id: string;
            /** Last Success At */
            readonly last_success_at?: string | null;
            /** Mapping Version Id */
            readonly mapping_version_id?: string | null;
            /** Revision */
            readonly revision: number;
            /** Schedule Revision */
            readonly schedule_revision?: number | null;
        };
        /** SpatialAssetInput */
        SpatialAssetInput: {
            /** Accuracy Metres */
            accuracy_metres?: number | null;
            /**
             * Artifact Version Id
             * Format: uuid
             */
            artifact_version_id: string;
            /**
             * Asset Id
             * Format: uuid
             */
            asset_id: string;
            /**
             * Captured At
             * Format: date-time
             */
            captured_at: string;
            /** Expected Revision */
            expected_revision: number;
            /** Footprint */
            footprint: {
                [key: string]: unknown;
            };
            /** Horizontal Datum */
            horizontal_datum: string;
            /**
             * Quality
             * @default unverified
             * @enum {string}
             */
            quality: "unverified" | "display_only" | "verified";
            /**
             * Resource Id
             * Format: uuid
             */
            resource_id: string;
            /** Sha256 */
            sha256: string;
            /** Source */
            source: string;
            /** Survey Evidence Version Id */
            survey_evidence_version_id?: string | null;
            /** Vertical Datum */
            vertical_datum?: string | null;
        };
        /** SpatialAssetRequest */
        SpatialAssetRequest: {
            asset: components["schemas"]["SpatialAssetInput"];
            /**
             * Bundle Id
             * Format: uuid
             */
            bundle_id: string;
        };
        /** SubmitDraftRequest */
        SubmitDraftRequest: {
            /** Impact Digest */
            impact_digest: string;
        };
        /**
         * TemporalFields
         * @description Explicit raw record paths. Null values are open bounds/unknown observation;
         *     missing configured fields are errors. CSV empty cells must use an explicit
         *     timestamp; an omitted path, rather than an empty string, declares an open bound.
         */
        TemporalFields: {
            /** Observed At Path */
            observed_at_path?: string | null;
            /** Valid From Path */
            valid_from_path?: string | null;
            /** Valid To Path */
            valid_to_path?: string | null;
        };
        /** Triple */
        Triple: {
            object: components["schemas"]["RdfTerm"];
            /** Predicate Iri */
            predicate_iri: string;
            /** Subject Iri */
            subject_iri: string;
        };
        /** TypePremiseRef */
        TypePremiseRef: {
            /** Class Iri */
            class_iri: string;
            declaration_revision: components["schemas"]["RevisionRef"];
        };
        /** UpdateDraftRequest */
        UpdateDraftRequest: {
            document: components["schemas"]["SchemaDocument"];
        };
        /** UpdateObjectRequest */
        UpdateObjectRequest: {
            /**
             * Bundle Id
             * Format: uuid
             */
            bundle_id: string;
            patch: components["schemas"]["FactPatch"];
        };
        /** UploadSessionCreate */
        UploadSessionCreate: {
            /** Filename */
            filename: string;
            /** Media Type */
            media_type: string;
            /** Metadata */
            metadata?: {
                [key: string]: unknown;
            };
            /** Previous Version Id */
            previous_version_id?: string | null;
            /**
             * Resource Id
             * Format: uuid
             */
            resource_id: string;
            /** Sha256 */
            sha256: string;
            /** Size Bytes */
            size_bytes: number;
        };
        /** UserDetail */
        UserDetail: {
            /** Display Name */
            display_name: string;
            /** Email */
            email: string | null;
            /** Enabled */
            enabled: boolean;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Issuer */
            issuer: string;
            /** Revision */
            revision: number;
            /** Subject */
            subject: string;
        };
        /** UserWrite */
        UserWrite: {
            /** Enabled */
            enabled: boolean;
        };
        /** ValidateFactsRequest */
        ValidateFactsRequest: {
            /**
             * Bundle Id
             * Format: uuid
             */
            bundle_id: string;
            create?: components["schemas"]["CreateObjectInput"] | null;
            /** Facts */
            facts?: components["schemas"]["AssertionInput"][] | null;
        };
        /** ValidationError */
        ValidationError: {
            /** Context */
            ctx?: Record<string, never>;
            /** Input */
            input?: unknown;
            /** Location */
            loc: (string | number)[];
            /** Message */
            msg: string;
            /** Error Type */
            type: string;
        };
        /** ValidationIssue */
        ValidationIssue: {
            /** Constraint Iri */
            constraint_iri: string;
            /** Focus Iri */
            focus_iri: string;
            /** Message */
            message: string;
            /** Path Iri */
            path_iri: string | null;
            /**
             * Severity
             * @enum {string}
             */
            severity: "Violation" | "Warning" | "Info";
            /** Shape Iri */
            shape_iri: string | null;
            value: components["schemas"]["RdfTerm"] | null;
        };
        /** ValidationReport */
        ValidationReport: {
            /**
             * Bundle Id
             * Format: uuid
             */
            bundle_id: string;
            /** Conforms */
            conforms: boolean;
            /** Evaluation Time */
            evaluation_time?: string | null;
            /** Focus Iris */
            focus_iris?: string[] | null;
            /**
             * Invocation Scope
             * @default dataset
             * @enum {string}
             */
            invocation_scope: "dataset" | "focused";
            /** Original Shapes Digest */
            original_shapes_digest?: string | null;
            report_artifact: components["schemas"]["BlobRef"] | null;
            /** Root Selection */
            root_selection?: {
                [key: string]: string[];
            } | null;
            /** Violations */
            violations: components["schemas"]["ValidationIssue"][];
        };
        /** WithdrawLinkRequest */
        WithdrawLinkRequest: {
            /**
             * Bundle Id
             * Format: uuid
             */
            bundle_id: string;
            decision_ref?: components["schemas"]["RevisionRef"] | null;
            /** Support Ids */
            support_ids: string[];
        };
        /** WriteReceipt */
        WriteReceipt: {
            /** Assertion Ids */
            assertion_ids: string[];
            /**
             * Projection State
             * @enum {string}
             */
            projection_state: "pending" | "synced" | "failed";
            revision: components["schemas"]["RevisionRef"];
        };
    };
    responses: never;
    parameters: never;
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
    listApprovals: {
        parameters: {
            query?: {
                cursor?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Page_ApprovalDetail_"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    submitApproval: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ApprovalWrite"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApprovalDetail"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    previewApproval: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PreviewWrite"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PreviewDetail"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    getApproval: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                approval_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApprovalDetail"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    decideApproval: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                approval_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ApprovalDecision"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApprovalDetail"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    uploadArtifact: {
        parameters: {
            query: {
                resource_id: string;
                filename: string;
                previous_version_id?: string | null;
            };
            header?: {
                "x-artifact-metadata"?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/octet-stream": string;
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ArtifactDetail"];
                };
            };
            /** @description Unauthorized */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Forbidden */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Not Found */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Conflict */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Request Entity Too Large */
            413: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Unprocessable Entity */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Precondition Required */
            428: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Service Unavailable */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
        };
    };
    createEvidence: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "multipart/form-data": {
                    /** Format: binary */
                    file: string;
                    metadata: components["schemas"]["EvidenceCreate"];
                };
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["EvidenceView"];
                };
            };
        };
    };
    getEvidence: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["EvidenceView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    createArtifactUpload: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["UploadSessionCreate"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Unauthorized */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Forbidden */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Not Found */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Conflict */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Request Entity Too Large */
            413: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Unprocessable Entity */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Precondition Required */
            428: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Service Unavailable */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
        };
    };
    getArtifactUpload: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                upload_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Unauthorized */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Forbidden */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Not Found */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Conflict */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Request Entity Too Large */
            413: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Unprocessable Entity */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Precondition Required */
            428: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Service Unavailable */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
        };
    };
    cancelArtifactUpload: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                upload_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Unauthorized */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Forbidden */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Not Found */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Conflict */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Request Entity Too Large */
            413: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Unprocessable Entity */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Precondition Required */
            428: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Service Unavailable */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
        };
    };
    completeArtifactUpload: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                upload_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ArtifactDetail"];
                };
            };
            /** @description Unauthorized */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Forbidden */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Not Found */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Conflict */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Request Entity Too Large */
            413: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Unprocessable Entity */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Precondition Required */
            428: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Service Unavailable */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
        };
    };
    putArtifactUploadPart: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                upload_id: string;
                number: number;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Unauthorized */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Forbidden */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Not Found */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Conflict */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Request Entity Too Large */
            413: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Unprocessable Entity */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Precondition Required */
            428: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Service Unavailable */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
        };
    };
    getArtifact: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                version_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ArtifactDetail"];
                };
            };
            /** @description Unauthorized */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Forbidden */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Not Found */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Conflict */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Request Entity Too Large */
            413: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Unprocessable Entity */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Precondition Required */
            428: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Service Unavailable */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
        };
    };
    downloadArtifact: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                version_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Unauthorized */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Forbidden */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Not Found */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Conflict */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Request Entity Too Large */
            413: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Unprocessable Entity */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Precondition Required */
            428: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Service Unavailable */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
        };
    };
    linkArtifact: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                version_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ArtifactLinkWrite"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ArtifactMutationDetail"];
                };
            };
            /** @description Unauthorized */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Forbidden */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Not Found */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Conflict */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Request Entity Too Large */
            413: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Unprocessable Entity */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Precondition Required */
            428: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Service Unavailable */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
        };
    };
    reviewArtifact: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                version_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ArtifactReviewWrite"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ArtifactMutationDetail"];
                };
            };
            /** @description Unauthorized */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Forbidden */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Not Found */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Conflict */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Request Entity Too Large */
            413: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Unprocessable Entity */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Precondition Required */
            428: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
            /** @description Service Unavailable */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiErrorDetail"];
                };
            };
        };
    };
    listConflicts: {
        parameters: {
            query?: {
                cursor?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Page_ConflictView_"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    getConflict: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ConflictView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    refreshConflictCandidates: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["RefreshConflictCommand"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ConflictView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    reevaluateConflictRuling: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ReviewRulingCommand"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RulingView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    listConflictRulings: {
        parameters: {
            query?: {
                cursor?: string | null;
            };
            header?: never;
            path: {
                id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Page_RulingView_"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    resolveConflict: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["RulingCommand"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RulingView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    listGeometry: {
        parameters: {
            query?: {
                bbox?: string | null;
                scope_id?: string | null;
                cursor?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["GeoFeatureCollection"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    getObjectGeometry: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                object_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["GeoFeatureCollection"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    updateObjectGeometry: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                object_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["GeometryWriteRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RevisionRef"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    listGrants: {
        parameters: {
            query?: {
                cursor?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Page_GrantDetail_"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    createGrant: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["GrantWrite"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["GrantDetail"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    revokeGrant: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                grant_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["GrantDetail"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    getGraphNeighborhood: {
        parameters: {
            query: {
                resource_id: string;
                hops?: number;
                cursor?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["GraphNeighborhood"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    getAssertionProof: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                assertion_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CurrentProofView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    listGroups: {
        parameters: {
            query?: {
                cursor?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Page_GroupDetail_"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    createGroup: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["GroupWrite"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["GroupDetail"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    updateGroup: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                group_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["GroupWrite"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["GroupDetail"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    listGroupMembers: {
        parameters: {
            query?: {
                cursor?: string | null;
            };
            header?: never;
            path: {
                group_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Page_UserDetail_"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    setGroupMembers: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                group_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["MembersWrite"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["MembersDetail"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    getHealthOverview: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HealthOverview"];
                };
            };
        };
    };
    listHistory: {
        parameters: {
            query?: {
                resource_id?: string | null;
                resource_kind?: string | null;
                actor_id?: string | null;
                task_id?: string | null;
                recorded_from?: string | null;
                recorded_to?: string | null;
                cursor?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Page_RevisionRef_"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    diffRevisions: {
        parameters: {
            query: {
                from_revision: number;
                to_revision: number;
            };
            header?: never;
            path: {
                resource_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RevisionDiff"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    getRevision: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                resource_id: string;
                revision: number;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RevisionDetail"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    listIdentityOperations: {
        parameters: {
            query?: {
                cursor?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Page_IdentityOperationDetail_"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    getIdentityOperation: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                operation_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["IdentityOperationDetail"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    resolveIdentityAlias: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": string;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    mergeIdentities: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["MergeCommand"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RevisionRef"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    previewIdentityMerge: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["MergePreviewCommand"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["MergeProposal"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    applyImport: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ApplyImportCommand"];
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["JobRef"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    previewImport: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ImportPreviewCommand"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PreviewReport"];
                };
            };
            /** @description Accepted */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["JobRef"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    getImportPreview: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PreviewReport"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    listImportPreviewRows: {
        parameters: {
            query?: {
                cursor?: string | null;
            };
            header?: never;
            path: {
                id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Page_RowResult_"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    getImport: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ImportRunView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    listImportRows: {
        parameters: {
            query?: {
                cursor?: string | null;
            };
            header?: never;
            path: {
                id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Page_RowResult_"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    listJobs: {
        parameters: {
            query?: {
                cursor?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Page_JobView_"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    getJob: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                job_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["JobView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    pauseJob: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                job_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["JobRef"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    resumeJob: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                job_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["JobRef"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    retryJob: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                job_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["JobRef"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    listLinks: {
        parameters: {
            query?: {
                cursor?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Page_LinkView_"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    createLink: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CreateLinkRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["WriteReceipt"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    withdrawLink: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["WithdrawLinkRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["WriteReceipt"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    registerLocationObservation: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["LocationObservationRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RevisionRef"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    listMapResources: {
        parameters: {
            query?: {
                cursor?: string | null;
                limit?: number | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["MapResourcePage"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    createMapResource: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["MapResourceCreate"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["MapResourceView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    getMapResource: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["MapResourceView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    updateMapResource: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["MapResourcePatch"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["MapResourceView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    getMapArchive: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/vnd.pmtiles": unknown;
                };
            };
            /** @description Partial Content */
            206: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/vnd.pmtiles": unknown;
                };
            };
            /** @description Invalid or unsatisfiable byte range */
            416: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    getMapAsset: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: string;
                asset_path: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    getMapStyle: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    getMapTile: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: string;
                z: number;
                x: number;
                y: number;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/vnd.mapbox-vector-tile": unknown;
                    "image/jpeg": unknown;
                    "image/png": unknown;
                    "image/webp": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    listObjects: {
        parameters: {
            query?: {
                cursor?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Page_ObjectView_"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    createObject: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CreateObjectRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["WriteReceipt"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    getObject: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ObjectView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    updateObject: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["UpdateObjectRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["WriteReceipt"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    listObjectAssertions: {
        parameters: {
            query?: {
                cursor?: string | null;
            };
            header?: never;
            path: {
                id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Page_AssertionView_"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    deactivateObject: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["DeactivateObjectRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["WriteReceipt"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    getObjectFormSchema: {
        parameters: {
            query?: {
                class_iri?: string | null;
            };
            header?: never;
            path: {
                id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["FormSchemaView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    listLocationObservations: {
        parameters: {
            query?: {
                start?: string | null;
                end?: string | null;
                cursor?: string | null;
            };
            header?: never;
            path: {
                object_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    listSpatialAssets: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                object_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    createSemanticExport: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ExportRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["JobRef"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    executeSparql: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["QueryRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["QueryResult"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    listRoles: {
        parameters: {
            query?: {
                cursor?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Page_RoleDetail_"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    createRole: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["RoleWrite"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RoleDetail"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    updateRole: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                role_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["RoleWrite"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RoleDetail"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    stageRuleConfiguration: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ConfigurationStageCommand"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ExecutionBundle"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    previewRuleImpact: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ImpactCommand"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RuleImpact"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    getRuleImpact: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RuleImpact"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    listRuleVersions: {
        parameters: {
            query?: {
                cursor?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Page_RuleVersion_"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    createRuleVersion: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["RuleVersion-Input"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RuleVersion-Output"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    getRuleVersion: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RuleVersion-Output"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    publishRules: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PublishRuleCommand"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RevisionRef"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    listSchemaDrafts: {
        parameters: {
            query?: {
                cursor?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Page_SchemaDraftView_"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    createSchemaDraft: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CreateDraftRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SchemaDraftView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    importSemanticDraft: {
        parameters: {
            query?: never;
            header?: {
                /** @description Required when target_draft_id is supplied; the sole expected revision source, quoted as "<revision>". New targets start at revision 0. */
                "If-Match"?: string;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SemanticDraftImportBody"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SchemaDraftView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    getSchemaDraft: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SchemaDraftView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    updateSchemaDraft: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["UpdateDraftRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SchemaDraftView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    getDraftFormSchema: {
        parameters: {
            query: {
                class_iri: string;
            };
            header?: never;
            path: {
                id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["FormSchemaView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    getDraftInheritance: {
        parameters: {
            query: {
                class_iri: string;
            };
            header?: never;
            path: {
                id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["InheritanceView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    submitSchemaDraft: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SubmitDraftRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApprovalRef"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    validateSchemaDraft: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ImpactReport"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    listSchemaReleases: {
        parameters: {
            query?: {
                cursor?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Page_SchemaReleaseView_"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    publishSchemaRelease: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PublishReleaseRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["JobRef"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    getSchemaRelease: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SchemaReleaseView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    diffSchemaRelease: {
        parameters: {
            query: {
                base_release_id: string;
            };
            header?: never;
            path: {
                id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SchemaDiffView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    getReleasedFormSchema: {
        parameters: {
            query: {
                class_iri: string;
            };
            header?: never;
            path: {
                id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["FormSchemaView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    getSession: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SessionDetail"];
                };
            };
        };
    };
    sessionCallback: {
        parameters: {
            query?: {
                state?: string | null;
                code?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            303: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    sessionLogin: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            302: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
        };
    };
    logoutSession: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["LogoutDetail"];
                };
            };
        };
    };
    refreshSession: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SessionDetail"];
                };
            };
        };
    };
    listSources: {
        parameters: {
            query?: {
                cursor?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Page_SourceView_"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    createSource: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SourceCreate"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SourceView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    getSource: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SourceView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    getSourceConnection: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ConnectionConfig-Output"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    configureSourceConnection: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ConnectionConfig-Input"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ConnectionConfig-Output"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    setSourceCredentials: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SourceCredentialInput"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CredentialRef"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    getSourceMapping: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["MappingProfile-Output"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    saveSourceMapping: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["MappingProfile-Input"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["MappingProfile-Output"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    getSourceSchedule: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ScheduleView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    configureSourceSchedule: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ScheduleCommand"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ScheduleView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    testSourceConnection: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ConnectionCheck"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    registerSpatialAsset: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SpatialAssetRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RevisionRef"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    listUsers: {
        parameters: {
            query?: {
                cursor?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Page_UserDetail_"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    inviteUser: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["InvitationWrite"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["IdentityOperationDetail"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    updateUser: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                user_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["UserWrite"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["IdentityOperationDetail"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    validateFacts: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ValidateFactsRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ValidationReport"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    live_health_live_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: string;
                    };
                };
            };
        };
    };
    ready_health_ready_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
        };
    };
}
