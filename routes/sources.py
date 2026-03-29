import logging

from flask import Blueprint, jsonify

from extensions import limiter
from services.source_service import source_service

log = logging.getLogger(__name__)

sources_bp = Blueprint('sources', __name__)


@sources_bp.route('/api/sources', methods=['GET'])
@limiter.limit("30 per minute")
def list_sources():
    try:
        return jsonify({'success': True, 'sources': source_service.public_list()}), 200
    except Exception:
        log.exception("list_sources failed")
        return jsonify({'error': 'Internal server error'}), 500
