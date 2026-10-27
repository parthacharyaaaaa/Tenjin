from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, cast

import orjson
import pytest
from auth_server.admin.roles import AdminRole
from auth_server.models.data_transport.admin import AdminPublicResult
from auth_server.models.data_transport.keydata import (
    ActiveKeyPrivateDataResult,
    ActiveKeyPublicDataResult,
    BasicKeyPublicDataResult,
    EllipticCurveJWKResult,
    ExpiredKeyDataResult,
    RotatedKeyDataResult,
)
from auth_server.models.data_transport.suspicious_activity import (
    SuspiciousActivityResult,
)
from auth_server.models.database import Admin, KeyData, SuspiciousActivity
from auxillary.security.data_structures.jwks.enums import ECAlg, JWKKty, JWKUse
from auxillary.security.serialization import pem_serialize_public_key
from auxillary.utils import cache_repr, json_repr, to_base64url
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.hazmat.primitives.serialization import PublicFormat

EPOCH = datetime(2026, 1, 1, tzinfo=UTC)
ROTATED_AT = datetime(2026, 2, 1, tzinfo=UTC)
EXPIRED_AT = datetime(2026, 3, 1, tzinfo=UTC)


def _public_pem() -> tuple[ec.EllipticCurvePublicKey, bytes]:
    public_key = ec.derive_private_key(7, ec.SECP256R1()).public_key()
    return public_key, pem_serialize_public_key(
        public_key, PublicFormat.SubjectPublicKeyInfo
    )


def _key_cache_mapping(**overrides: Any) -> dict[str, Any]:
    _, public_pem = _public_pem()
    mapping: dict[str, Any] = {
        "kid": "key-1",
        "alg": ECAlg.ES256,
        "curve": "secp256r1",
        "epoch": EPOCH.isoformat(),
        "public_pem": public_pem.decode("ascii"),
    }
    mapping.update(overrides)
    return {key: orjson.dumps(value) for key, value in mapping.items()}


@pytest.mark.parametrize(
    ("dto_type", "state_fields"),
    (
        (
            ActiveKeyPublicDataResult,
            {
                "rotated_out_at": None,
                "expired_at": None,
                "manual_rotation": None,
                "rotated_by": None,
            },
        ),
        (
            RotatedKeyDataResult,
            {
                "rotated_out_at": ROTATED_AT.isoformat(),
                "expired_at": None,
                "manual_rotation": True,
                "rotated_by": 4,
            },
        ),
        (
            ExpiredKeyDataResult,
            {
                "rotated_out_at": ROTATED_AT.isoformat(),
                "expired_at": EXPIRED_AT.isoformat(),
                "manual_rotation": False,
                "rotated_by": None,
            },
        ),
    ),
)
def test_public_key_dto_cache_round_trip(
    dto_type: type[BasicKeyPublicDataResult],
    state_fields: Mapping[str, Any],
) -> None:
    dto = dto_type.construct_from_cache(_key_cache_mapping(**state_fields))
    serialized = cache_repr(dto)

    assert dto_type.construct_from_cache(cast(Mapping[str, Any], serialized)) == dto
    assert isinstance(dto.public_pem, bytes)
    assert isinstance(serialized["public_pem"], bytes)


def test_private_key_cache_round_trip_and_public_copy_redact_private_pem() -> None:
    mapping = _key_cache_mapping(
        rotated_out_at=None,
        expired_at=None,
        manual_rotation=None,
        rotated_by=None,
        private_pem="private-pem",
    )

    private_result = ActiveKeyPrivateDataResult.construct_from_cache(mapping)
    serialized = cache_repr(private_result)
    public_result = private_result.create_public_copy()

    assert (
        ActiveKeyPrivateDataResult.construct_from_cache(
            cast(Mapping[str, Any], serialized)
        )
        == private_result
    )
    assert private_result.private_pem == b"private-pem"
    assert serialized["private_pem"] == orjson.dumps("private-pem")
    assert isinstance(public_result, ActiveKeyPublicDataResult)
    assert not hasattr(public_result, "private_pem")
    assert cache_repr(public_result) == {
        key: value for key, value in serialized.items() if key != "private_pem"
    }


def test_key_json_representation_serializes_binary_and_datetime_fields() -> None:
    result = RotatedKeyDataResult.construct_from_cache(
        _key_cache_mapping(
            rotated_out_at=ROTATED_AT.isoformat(),
            expired_at=None,
            manual_rotation=True,
            rotated_by=9,
        )
    )

    serialized = json_repr(result)

    assert serialized["public_pem"] == result.public_pem.decode("utf-8")
    assert serialized["epoch"] == "2026-01-01T00:00:00Z"
    assert serialized["rotated_out_at"] == "2026-02-01T00:00:00Z"
    assert serialized["expired_at"] is None


def test_key_dto_constructs_from_orm_with_state_specific_fields() -> None:
    _, public_pem = _public_pem()
    orm_key = KeyData(
        kid="rotated-key",
        alg=ECAlg.ES256,
        curve="secp256r1",
        epoch=EPOCH,
        public_pem=public_pem,
        private_pem=None,
        rotated_out_at=ROTATED_AT,
        expired_at=None,
        manual_rotation=True,
        rotated_by=3,
    )

    result = RotatedKeyDataResult.construct_from_orm(orm_key)

    assert result.kid == orm_key.kid
    assert result.public_pem == public_pem
    assert result.rotated_out_at == ROTATED_AT
    assert result.expired_at is None
    assert result.rotated_by == 3


def test_as_jwk_derives_public_coordinates_and_metadata() -> None:
    public_key, public_pem = _public_pem()
    result = ActiveKeyPublicDataResult.construct_from_cache(
        _key_cache_mapping(
            public_pem=public_pem.decode("ascii"),
            rotated_out_at=None,
            expired_at=None,
            manual_rotation=None,
            rotated_by=None,
        )
    )
    numbers = public_key.public_numbers()

    assert result.as_jwk() == EllipticCurveJWKResult(
        alg=ECAlg.ES256,
        crv="secp256r1",
        kid="key-1",
        x=to_base64url(numbers.x),
        y=to_base64url(numbers.y),
        kty=JWKKty.EC,
        use=JWKUse.SIG,
    )


def test_as_jwk_rejects_non_elliptic_curve_public_key() -> None:
    public_key = rsa.generate_private_key(
        public_exponent=65537, key_size=2048
    ).public_key()
    result = ActiveKeyPublicDataResult.construct_from_cache(
        _key_cache_mapping(
            public_pem=public_key.public_bytes(
                encoding=serialization.Encoding.PEM,
                format=PublicFormat.SubjectPublicKeyInfo,
            ).decode("ascii"),
            rotated_out_at=None,
            expired_at=None,
            manual_rotation=None,
            rotated_by=None,
        )
    )

    with pytest.raises(TypeError, match="Expected an elliptic-curve public key"):
        result.as_jwk()


def test_jwk_initializes_from_cached_mapping() -> None:
    mapping = {
        "alg": ECAlg.ES256,
        "crv": "secp256r1",
        "kid": "key-1",
        "x": "x-coordinate",
        "y": "y-coordinate",
        "kty": JWKKty.EC,
        "use": JWKUse.SIG,
    }

    assert EllipticCurveJWKResult.initialize_from_cache(mapping) == (
        EllipticCurveJWKResult(
            alg=ECAlg.ES256,
            crv="secp256r1",
            kid="key-1",
            x="x-coordinate",
            y="y-coordinate",
            kty=JWKKty.EC,
            use=JWKUse.SIG,
        )
    )


def test_admin_dto_orm_json_and_cache_serialization() -> None:
    orm_admin = Admin(
        id_=5,
        username="staff_user",
        role=AdminRole.STAFF,
        password_hash=b"not-exposed",
        time_deleted=None,
        last_login=EPOCH,
        locked=True,
        created_by=1,
    )

    result = AdminPublicResult.construct_from_orm(orm_admin)
    cached = cache_repr(result)

    assert result.id == orm_admin.id_
    assert not hasattr(result, "password_hash")
    assert json_repr(result) == {
        "id": 5,
        "username": "staff_user",
        "role": AdminRole.STAFF,
        "time_deleted": None,
        "last_login": "2026-01-01T00:00:00Z",
        "locked": True,
        "created_by": 1,
    }
    assert cached["time_deleted"] == orjson.dumps(None)
    assert cached["locked"] == orjson.dumps(True)
    assert (
        AdminPublicResult.construct_from_cache(cast(Mapping[str, Any], cached))
        == result
    )


def test_suspicious_activity_dto_orm_json_and_cache_round_trip() -> None:
    orm_activity = SuspiciousActivity(
        id_=8,
        suspect=5,
        time_logged=EPOCH,
        description="Repeated invalid refresh digest",
    )

    result = SuspiciousActivityResult.construct_from_orm(orm_activity)
    cached = cache_repr(result)

    assert json_repr(result) == {
        "id": 8,
        "suspect": 5,
        "time_logged": "2026-01-01T00:00:00Z",
        "description": "Repeated invalid refresh digest",
    }
    assert (
        SuspiciousActivityResult.construct_from_cache(cast(Mapping[str, Any], cached))
        == result
    )
