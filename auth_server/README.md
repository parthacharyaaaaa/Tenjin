# Auth Server
This documentation covers the working of the auth server, including its Flask endpoints as well as its `TokenManager` class and the app factory.

## Table of Contents

1. [App Factory](#app-factory)
   1.1 [Application Initialization](#11-application-initialization)
   1.2 [Path Configuration](#12-path-configuration)
   1.3 [Core Error Handling](#13-core-error-handling)
   2.1 [Database and Migration Setup](#21-database-and-migration-setup)
   2.2 [Redis Configuration and Initialization](#22-redis-configuration-and-initialization)
   3.1 [Token Manager Setup (Master vs. Slave Workers)](#31-token-manager-setup-master-vs-slave-workers)
   3.1.1 [Slave Worker Behavior](#311-slave-worker-behavior)
   3.1.2 [Master Worker Behavior](#312-master-worker-behavior)
   4.1 [Blueprint Registration](#41-blueprint-registration)

2. [Token Manager](#token-manager)
   1.1 [Initialization and Setup](#11-initialization-and-setup)
   1.2 [Token Structure and Universal Parameters](#12-token-structure-and-universal-parameters)
   1.3 [Secure Replay Mitigation Strategy](#13-secure-replay-mitigation-strategy)
   1.4 [Issuance and Reissuance](#14-issuance-and-reissuance)
   1.5 [Blacklisting and Family Invalidation](#15-blacklisting-and-family-invalidation)
   1.6 [Polling and Cluster Key Sync](#16-polling-and-cluster-key-sync)
   1.7 [Defensive Failures and Observability](#17-defensive-failures-and-observability)
   1.8 [Key Rotation Support](#18-key-rotation-support)

3. [API Documentation](#api-documentation)
   • [Blueprint: auth (`/auth`)](#blueprint-auth-url-prefix-auth)
   • [Blueprint: cmd (`/cmd`)](#blueprint-cmd-url-prefix-cmd)


## App Factory
This section documents the application factory function `create_app()` for the authentication server. The app factory plays the central role in initializing the server, performing secure key material bootstrapping, extension setup, and conditional logic for primary vs. secondary workers.

**1.1 Application Initialization**
The `Flask` application is instantiated with a named context `auth_app`, specifying `instance_path` and `static_folder`. These directories are used later to locate runtime-specific resources (like encrypted PEMs, JWKS, and logs). The app configuration is immediately loaded from a centralized `flaskconfig` object, and the app's process ID is cached to aid in master-worker orchestration logic later on.

**1.2 Path Configuration**
Custom config variables are added to Flask’s configuration object to set paths for:

* The JWKS file (`JWKS_FPATH`) used in public key distribution
* Public PEM storage directory under static assets
* Encrypted private PEM storage directory under instance folder

Notably, a cautionary comment questions whether storing encrypted private PEMs in the `instance` folder is appropriate, indicating awareness of separation of secrets and code—something Kubernetes/KMS can later address via mountable secrets.

**1.3 Core Error Handling**

A catch-all error handler is registered for all exceptions. This allows for custom structured responses even during fatal or unexpected failures.

---

**2.1 Database and Migration Setup**

SQLAlchemy is initialized with the app context, and Flask-Migrate is used to handle migrations. Models (notably `KeyData`) are imported and later used extensively in the boot process to read/write key state.

**2.2 Redis Configuration and Initialization**

The app loads a TOML config for Redis setup from a path determined by environment variables. Redis is used via two separate configurations:

* `token_store`: For storing JWTs
* `synced_store`: acts as a coordination layer across multiple workers (for locks, state syncing, etc.) as well as a session storage for server admins

After injecting secrets into the Redis configs, two interfaces are initialized:

* `init_redis()` for token-specific Redis
* `init_syncedstore()` for globally consistent Redis use across workers

---

### 3.1 Token Manager Setup (Master vs. Slave Workers)

The app determines whether the current worker process is the *master bootup* worker by using a Redis lock (`AUTH_BOOTUP_MASTER`). This ensures that only one worker touches JWKS and performs file operations, while others defer.

#### 3.1.1 Slave Worker Behavior

Slave workers wait until the master bootup is complete. Then, they load valid keys from the database, distinguish the active key from rotated keys, and initialize the `token_manager` using only in-memory mappings. All file I/O is skipped.

#### 3.1.2 Master Worker Behavior

If the current worker wins the Redis lock, it assumes responsibility for synchronizing all cryptographic material:

* Reads valid keys from DB. If none exist, generates a new ECDSA keypair.
* Writes encrypted private key to disk, and generates a public PEM file.
* Constructs the initial JWKS payload by decoding the public ECDSA coordinates to base64url.
* Cleans up stale or expired key files on disk.
* Updates the `token_manager` with both active and verification keys.
* Commits JWKS file to disk.
* Ensures the Redis list `VALID_KEYS` is correct.

If this process fails, a Redis `ABORT` flag is set to prevent further boot, and the error trace is printed. Cleanup also ensures that once the master boot is successful or fails, the lock (`AUTH_BOOTUP_MASTER`) is released so slave workers can continue or abort accordingly.

---

### 4.1 Blueprint Registration

After the bootup process, the `auth` and `cmd` blueprints are registered with a versioned URL prefix (e.g., `/api/v1/cmd`), completing the application setup.

## Token Manager
### 1.1 Initialization and Setup

The `TokenManager` class is the security-critical core responsible for issuing, verifying, and rotating JWT-based access and refresh tokens. It is initialized with a Redis interface for per-session state tracking, a Redis-synced store for cluster-wide coordination, and an SQLAlchemy-managed database to persist key material. During initialization, a live mapping of all currently valid public/private key pairs is built, combining the active signing key and any past verification keys. It also boots up a background polling thread to ensure the local key state remains in sync with the shared key state across processes.

### 1.2 Token Structure and Universal Parameters

Both access and refresh tokens are signed using ECDSA with customizable headers and claims. Universal claims (`uClaims`) and headers (`uHeaders`) are automatically merged into every issued token, ensuring consistency. Token type (`typ`), signing algorithm (`alg`), and leeway (used during verification) are all configurable at initialization. The `max_tokens_per_fid` parameter limits how many refresh tokens a session (family ID) may have simultaneously, preventing token overpopulation.

### 1.3 Secure Replay Mitigation Strategy

Unlike traditional RESTful token issuance, this design uses a non-idempotent, stateful approach to guard against refresh token replay attacks. When a refresh token is re-used, the token family is checked against Redis. If the JTI and exp metadata don’t match expectations, the entire token family is invalidated, effectively locking out the attacker. This mechanism ensures zero replay tolerance—if even a single refresh token is replayed (e.g., stolen and used), every other sibling token is instantly revoked. This sacrifices RESTful statelessness for airtight session control and high security.

### 1.4 Issuance and Reissuance

Access tokens are short-lived and contain all necessary claims for authorization. Refresh tokens are longer-lived and act as renewable session handles. When a refresh token is re-used (`reissueTokenPair()`), the system ensures the original token’s authenticity by validating its metadata from Redis before allowing a new token to be generated. Every issuance is tied to a session-scoped "family ID" (FID), and the `shiftTokenWindow()` logic enforces a rolling window limit on how many refresh tokens can exist per FID at once.

### 1.5 Blacklisting and Family Invalidation

Families are stored as Redis lists (`FID:<id>`), enabling atomic expiration and trimming. When a refresh token is deemed compromised or improperly re-used, `invalidateFamily()` is called, deleting the entire Redis key representing that session. This centralizes revocation and avoids having to blacklist individual token JTIs. Because these lists are transient and keyed to the expiration timestamp of the tokens they represent, this design eliminates memory bloat over time.

### 1.6 Polling and Cluster Key Sync

The `poll_store()` function runs in a background daemon thread, polling the shared Redis-synced store every 10 seconds for changes to the valid keys list. If new keys are introduced (e.g., due to rotation by the master process), they are fetched from the database and merged into the local key map. Likewise, keys that have been removed globally are purged from local memory. This ensures each worker maintains a secure, minimal keyset that reflects the current state of trust.

### 1.7 Defensive Failures and Observability

Decoding tokens fails fast on malformed or expired headers. If a token fails due to time-related or key-related issues, an optional invalidation is triggered to prevent future reuse of compromised sessions. All major Redis and DB interactions are wrapped in try-except blocks, and the background poller provides detailed logs of key syncing behavior and any failures in a resilient loop.

### 1.8 Key Rotation Support

The `update_keydata()` method allows for runtime key rotation. Upon rotating a signing key, it’s automatically added to the verification pool for backward compatibility. Old keys are retained up to `max_valid_keys` and expired keys are asynchronously purged. The manager ensures that the active key used for signing is never invalidated during runtime to maintain uninterrupted token generation.

## API Documentation
### Blueprint: auth (url prefix: `/auth`)
```http
GET /api/v1/auth/jwks.json
```
#### Description: Send the contents of the current JWKS file
#### Response JSON
Status: 200 OK
```json
{
  "keys": [
    {
      "kty": "EC",
      "alg": "ES256",
      "crv": "SECP256k1",
      "use": "sig",
      "kid": "9529697",
      "x": "02cyvgL0j__Lh4-F5nBIz8Fej-NKPvMPqi7DB0lJDmw",
      "y": "5VvkzzmzaO--25rc1YHBuoPkuSLRsvIzvlKz2YqTk1o"
    },
    {
      "kty": "EC",
      "alg": "ES256",
      "crv": "SECP256k1",
      "use": "sig",
      "kid": "2435047",
      "x": "5BR0HBMuRRh5smsZRrOt3QTJemQxqcXKUMVqJH-75Ok",
      "y": "vKyPhqyGf6ANMGRRYSs22DMYRGibgg5OlibdI2GCEb4"
    }
  ]
}
```

# 
```http
POST /api/v1/auth/login
```
#### Description:
This endpoint serves as the client-exposed endpoint for logging into the resource server
#### Request JSON
```json
{
    "identity" : "Foo123456789",
    "password" : "topsecretpassword"
}
```
#### Working:
A POST request is sent to the resource server with request's JSON contents for actual verification. Upon success, the response from the resource server is expected to be as follows:

Status: 200 OK
```json
{
    "message" : "Login complete.",
    "sub" : "Foo123456789",
    "sid" : 12345,
}
```
Based on `sid` and `sub` claims, a unique ID for this user's token family is generated. This ID is used by the TokenManager to issue a token pair, which are attached as cookies in the outgoing response.

#### Response JSON
```json
{
        "message" : "Login complete.",
        "username" : "Foo123456789",
        "time_of_issuance" : 1750933045.2988706,
        "access_exp" : 1750953045.2988706,
        "leeway" : 300,
        "issuer" : "tenjin-auth-service"
}
```
#
```http
POST /api/v1/auth/register
```
#### Description:
This endpoint serves as the client-exposed endpoint for creating a new account
#### Request JSON
```json
{
    "username" :"Foo123456789",
    "password" : "topsecretpassword",
    "cpassword" : "topsecretpassword",
    "email" : "FooFoo.123@email.tld"
}
```
#### Working:
A POST request is sent to the resource server's endpoint for account creation, where the actual logic for account creation resides. The expected response from the resource server is:
```json
{
    "sub" : "Foo123456789",
    "sid" : 12345,
    "message" : "Registration complete.",
    "_additional" : {}
}
```

Similiar to login, a token family ID is made from the `sub` and `sid` claims and used to issue a token pair for this user. These tokens are attached as cookies to the outgoing response.
#### Response JSON
Status: 201 Created
```json
{
    "message" : "Registration complete.",
    "username" : "Foo123456789",
    "email" : "FooFoo.123@email.tld",
    "time_of_issuance" : 1750933045.2988706,
    "access_exp" : 1750953045.2988706,
    "leeway" : 300,
    "issuer" : "tenjin-auth-service",
    "_additional" : {}
}
```
#
```http
DELETE /api/v1/auth/tokens
```
#### Description:
Purge a token family
#### Request JSON
```json
{
    "sub" : "Foo123456789",
    "sid" : 12345
}
```
#### Working
After computing family ID from the JSON payload, `TokenManager.invalidateFamily` is called. This endpoint is used in cases like logout, replay attacks, and account deletions.
#### Response JSON
Status: 200 OK
```json
{
    "message" : "Token family purged",
    "family_id" : "8c7228e249329e7096ffc6d4ae4bb27b24e92b2a37fd0165328c25da7706df15"
}
```

#
```http
POST /api/v1/auth/reissue
```
#### Description:
Issue a new token pair based on a given refresh token. This invokes `TokenManager.reissueTokenPair()`

#### Response JSON
```json
{
    "message" : "Reissuance successful",
    "time_of_issuance" : 1750933045.2988706,
    "access_exp" : 1750953045.2988706,
    "leeway" : 300,
    "issuer" : "tenjin-auth-service",
}
```

### Blueprint: cmd (url prefix: `/cmd`)
Endpoints concerned with the internal working of the auth server are contained in this blueprint. This involves admin operations and key management
#
```http
POST /api/v1/cmd/admins/login
```
#### Description: Login to an existing admin account
#### Request JSON
```json
{
    "identity" : "admin123",
    "password" : "supersecretpassword"
}
```
#### Working
It is important to note that this endpoint also calls `report_suspicious_activity` on admin accounts being locked, or on incorrect passwords. However, the `force_logout` arg is set to False in these cases. 
Upon proper verification (account exists, is unlocked, and passwords match), a session key is made from this admin's id as `session:id`. Redis is consulted to check whether this admin has an active session or not. If found, the session is terminated and `report_suspicious_activity` is called.

A newly created session is created as a Redis hashmap with the following claims:
```json
{
    "admin_id" : 1,
    "session_iteration" : 1,
    "epoch" :1750933045.2988706,
    "expiry_at" : 1750953045.2988706,
    "role" : "super"
}
```
Sessions are stored in memory itself, primarily because traffic or admin-related endpoints will be very low, and we don't need to distribute authentication responsibilities to the client as we usually would with JWTs. A unique revival digest is also created, and is provided separately in the response, while the session stored in memory has this digest as one of its fields. In case an admin needs to refresh their session past the usual lifespan, they need to provide this revival digest again along with their session token

#### Response JSON
Status: 200 OK
```json
{
    "session_token" : "44bcebd1926f7a8c27bc7b909c36e503822...",
    "revival_digest" : "5cc5601fbcc007ac7a655d808ddce8e29a0..."
}
```

#
```http
POST /api/v1/cmd/admins
```
#### Description: Create a new admin user
#### Role Required: Super
#### Request JSON
```json
{
    "username" : "newadmin123",
    "password" : "supersecretpassword"
}
```

#### Working:
Very straighforwawrd working, if an admin with this username is not found, then a new admin account is created by hashing the provided password. Admins created through this endpoint will always have the lowest admin role assigned to them, i.e. `admin` and not `super`

#### Response JSON
```json
{
    "message" : "admin created"
}
```

#

```http
DELETE /api/v1/cmd/admins
```
#### Description: Delete an existing admin account
#### Role Required: Super
#### Request JSON

```json
{
    "id": 4
}
```

#### Working:
Marks an existing admin account as deleted by setting its `time_deleted` field. The admin account must exist and must not already be marked as deleted.
Deletion is **not** allowed for admins with role `super`.
Errors are raised in case of missing ID, nonexistent accounts, or deletion failure.

#### Response JSON
Status: 200 OK
```json
{
    "message": "Admin deleted"
}
```
#


```http
PATCH /api/v1/cmd/admins/logout
```

#### Description: Logout the currently logged-in admin
#### Role Required: Any
#### Working:
Deletes the admin’s current session token from memory (Redis store).
No revival digest is required to logout.

#### Response JSON
Status: 200 OK
```json
{
    "message": "Logout successful"
}
```
#

```http
POST /api/v1/cmd/admins/refresh
```

#### Description: Refresh the current admin session
#### Role Required: Any
#### Request JSON

```json
{
    "refresh-digest": "5cc5601fbcc007ac7a655d808ddce8e29a0..."
}
```

#### Working:

Enforces a strict maximum number of session refreshes (`session_iteration`) before requiring full reauthentication.
Validates that the provided `refresh-digest` matches the current session's revival digest.
If valid, the session is refreshed with updated expiry and session iteration, and a new digest is generated.
If the maximum number of refreshes is hit, or the digest is invalid, reauthentication is required.
In suspicious cases, `report_suspicious_activity` is called.

#### Response JSON

Status: 200 OK

```json
{
    "session_token": "a3bcebd1ba4f9a3d91f1e1...",
    "revival_digest": "d47ee0fbbe6509ab9aa15788c7c8e78df32..."
}
```

#

```http
POST /api/v1/cmd/admins/locks
```

#### Description: Lock a staff admin account

#### Role Required: Super

#### Request JSON

```json
{
    "id": 4
}
```

#### Working:

Locks an admin account by setting its `locked` field to `true`.
Only allowed if the admin is not already locked.
A locked admin is forcibly logged out by deleting their session from memory.
If the account is already locked, the response includes a `Conflict` with a link to unlock the account.

#### Response JSON

Status: 200 OK

```json
{
    "message": "Admin locked succesfully"
}
```

#

```http
DELETE /api/v1/cmd/admins/locks
```

#### Description: Unlock a staff admin account

#### Role Required: Super

#### Request JSON

```json
{
    "id": 4
}
```

#### Working:

Unlocks an admin account by setting its `locked` field to `false`.
Only allowed if the account is actually locked.
If the account is already unlocked, the response includes a `Conflict` with a link to lock the account.

#### Response JSON

Status: 200 OK

```json
{
    "message": "Admin unlocked succesfully"
}
```
#

```http
GET /api/v1/cmd/keys/<kid>
```

#### Description: Retrieve full key details, excluding private key

#### Role Required: Any admin

#### Working:

Fetches a key using the given `kid`. If found, returns all metadata apart from private key
Raises `404 Not Found` if the key doesn’t exist.

#### Response JSON

Status: 200 OK

```json
{
  "kid": "abc123",
  "curve": "secp256k1",
  "created_at": "2025-06-26T12:00:00Z",
  "rotated_out_at": null,
  "expired_at": null,
  "public_pem": "#--BEGIN PUBLIC KEY--#..."
}
```

#

```http
DELETE /api/v1/cmd/keys/<kid>
```
#### Description: Invalidate a key that has already been rotated out
#### Role Required: Super
#### Working:
* Uses a Redis lock to prevent concurrent invalidation attempts on the same key.
* Checks that the key exists and has already been rotated out (i.e., not active anymore).
* Denies invalidation if:

  * The key doesn’t exist → `404`
  * It is still active → `409 Conflict` + activity report
  * Already expired → `409 Conflict`

If allowed, the following happens:

* The key is marked as expired in the DB.
* The JWKS is updated by removing the key entry.
* Its corresponding public PEM file is deleted.
* In case of failure (e.g. file/DB error), a rollback is performed:

  * DB rolled back
  * JWKS restored
  * PEM regenerated
* Finally, the Redis `VALID_KEYS` list is updated to reflect the change, falling back to DB query if the state is corrupted.

#### Response JSON

Status: 200 OK

```json
{
  "message": "Key invalidated successfully",
  "purged_kid": "abc123",
  "valid_keys": ["def456", "ghi789"],
  "jwks_integrity_warning": "This key ID was not found in JWKS",
  "keylist_integrity_warning": "Synced keylist state was inconsistent and hence regenerated through database"
}
```

#

```http
DELETE /api/v1/cmd/keys/clean
```
#### Description: Invalidate **all inactive** keys (i.e., all except the currently active key)
#### Role Required: Super
#### Working:

* Uses a global Redis lock to avoid concurrent clean operations.
* If only one key is active (nothing to clean), raises `409 Conflict`.
* Preloads:

  * JWKS
  * All PEMs (for rollback)
* DB is updated to mark all rotated-but-not-expired keys as expired.
* JWKS is trimmed to only the current active key.
* PEM files of expired keys are deleted.
* In case of failure:

  * DB rollback
  * JWKS restoration
  * PEM regeneration

Finally, the Redis `VALID_KEYS` list is reset to just the active key.

#### Response JSON

Status: 200 OK

```json
{
  "message": "All inactive keys have been invalidated",
  "invalidated keys": ["old1", "old2"],
  "active_key": "newActive123"
}
```
#

```http
POST /api/v1/cmd/keys/rotate
```
#### Description: Trigger a key rotation
#### Role Required: Any admin
#### Working:
* Uses a Redis lock to prevent concurrent rotations.
* Applies a **cooldown** (stored in Redis) for `staff` admins.

  * If violated → raises `409 Conflict` and calls `report_suspicious_activity`

If rotation proceeds:

1. Previous active key is marked as rotated in DB.
2. New key pair is generated (ECDSA secp256k1).
3. New key is inserted into DB.
4. If the valid key count exceeds `MAX_VALID_KEYS`, the oldest key is expired and its PEMs are deleted.

**After DB commit:**

* JWKS updated with the new key (auto-trims to capacity).
* PEM files written (private and public).
* Old private PEM is removed.
* In-memory token manager updated with the new key.
* Redis `VALID_KEYS` updated.
* Rotation cooldown is enforced.

If Redis or DB states are inconsistent (e.g. missing key in VALID\_KEYS), fallback to DB is triggered to regenerate state.

#### Response JSON

Status: 201 Created

```json
{
  "message": "Key rotation successful",
  "kid": "new123",
  "public_pem": "#--BEGIN PUBLIC KEY#--...",
  "epoch": 1750950000.512,
  "alg": "ES256",
  "previous_kid": "old456"
}
```
