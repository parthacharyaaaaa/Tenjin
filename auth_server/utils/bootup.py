from typing import Mapping

from flask import Flask, Blueprint

from auth_server.blueprints import URLPrefix


def register_blueprints(
    app: Flask,
    blueprint_mapping: Mapping[Blueprint, URLPrefix],
    common_prefix: str = "",
) -> None:
    for blueprint, url_prefix in blueprint_mapping.items():
        app.register_blueprint(
            blueprint, url_prefix="/".join((common_prefix, url_prefix.value))
        )
