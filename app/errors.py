"""Public failure codes and reviewed messages; never expose exception payloads."""
import httpx
from pydantic import ValidationError
from sqlalchemy.exc import DBAPIError
import psycopg

MESSAGES = {
    'speech_not_detected': '녹음에서 목소리를 인식하지 못했어요. 마이크가 켜져 있는지 확인하고 조금 더 가까이에서 다시 녹음해 주세요.',
    'audio_empty': '녹음된 음성이 없어요. 녹음 버튼을 누른 뒤 말씀하고 다시 보내 주세요.',
    'audio_unreadable': '음성 파일을 읽을 수 없어요. 브라우저에서 다시 녹음해 보내 주세요.',
    'audio_too_large': '음성 파일이 너무 커서 보낼 수 없어요. 짧게 나누어 다시 녹음해 주세요.',
    'audio_too_long': '음성이 허용된 녹음 시간보다 길어요. 짧게 나누어 다시 녹음해 주세요.',
    'text_too_long': '입력 문장이 너무 길어요. 짧게 나누어 다시 보내 주세요.',
    'request_timeout': '요청을 처리하는 데 시간이 오래 걸려 중단됐어요. 잠시 후 다시 시도해 주세요.',
    'rate_limited': '현재 요청이 많아 서비스의 이용 한도에 도달했어요. 잠시 기다린 뒤 다시 시도해 주세요.',
    'connection_failed': '처리에 필요한 서비스에 연결하지 못했어요. 잠시 후 다시 시도해 주세요.',
    'service_unavailable': '연결된 서비스가 일시적으로 응답하지 않아요. 잠시 후 다시 시도해 주세요.',
    'service_authentication': '서비스 연결 인증에 문제가 있어 처리하지 못했어요. 문제가 계속되면 관리자에게 알려 주세요.',
    'service_configuration': '서비스 설정을 확인해야 해서 처리하지 못했어요. 관리자에게 알려 주세요.',
    'response_invalid': 'AI 답변을 올바른 형식으로 받지 못했어요. 잠시 후 다시 시도해 주세요.',
    'response_empty': 'AI가 답변을 반환하지 않았어요. 잠시 후 다시 시도해 주세요.',
    'storage_failed': '대화 기록을 읽거나 저장하는 중 문제가 생겼어요. 대화를 새로고침해 처리 상태를 확인해 주세요.',
    'resource_unavailable': '처리에 필요한 파일을 불러올 수 없어요. 문제가 계속되면 관리자에게 알려 주세요.',
    'queue_expired': '대기 중인 요청이 많아 답변을 시작하기 전에 대기 시간이 지났어요. 잠시 후 다시 보내 주세요.',
    'worker_interrupted': '처리 중 서버 작업이 중단됐어요. 잠시 후 다시 시도해 주세요.',
    'resource_busy': '다른 요청을 처리하고 있어 지금은 시작할 수 없어요. 잠시 후 다시 시도해 주세요.',
    'invalid_input': '보낸 내용을 처리할 수 없어요. 입력이나 녹음을 확인한 뒤 다시 보내 주세요.',
    'unexpected_error': '처리 중 예상하지 못한 문제가 생겼어요. 잠시 후 다시 시도하고, 계속되면 관리자에게 알려 주세요.',
}

# Older jobs contain class names rather than a domain failure code.
ALIASES = {
    'TimeoutError': 'request_timeout', 'APITimeoutError': 'request_timeout',
    'ReadTimeout': 'request_timeout', 'ConnectTimeout': 'request_timeout',
    'RateLimitError': 'rate_limited', 'ConnectionError': 'connection_failed',
    'APIConnectionError': 'connection_failed', 'ConnectError': 'connection_failed',
    'AuthenticationError': 'service_authentication', 'PermissionDeniedError': 'service_authentication',
    'InternalServerError': 'service_unavailable', 'ValidationError': 'response_invalid',
    'OutputParserException': 'response_invalid', 'ValueError': 'invalid_input',
    'OperationalError': 'storage_failed', 'InterfaceError': 'storage_failed',
    'FileNotFoundError': 'resource_unavailable', 'stt_empty_with_signal': 'speech_not_detected',
    'ModelBusy': 'resource_busy', 'PermissionError': 'service_configuration',
}

INPUT_MESSAGES = {
    '아이디는 영문 소문자·숫자·_.-로 3~64자여야 합니다.',
    '비밀번호는 8~24자여야 합니다.', '사용할 수 없는 아이디입니다.',
    '대화 제목을 입력해 주세요.', '같은 요청 번호에 다른 내용을 사용할 수 없습니다.',
    '종료된 대화입니다.', '이 대화의 이전 응답을 처리 중입니다.',
    '진행 중인 요청이 많습니다. 기존 응답이 끝난 후 보내주세요.',
    '요청이 많습니다. 잠시 후 다시 보내주세요.',
    '진행 중인 응답이 끝나면 대화를 마칠 수 있습니다.',
    '개인정보 처리와 72시간 보관에 동의해 주세요.',
    '복구 링크가 올바르지 않거나 만료되었습니다.',
    '문항 번호가 올바르지 않습니다.', '보고서가 완성된 후 제출해 주세요.',
    '다른 시험이 진행 중입니다. 잠시 후 다시 요청하세요.', '평가를 다시 요청할 수 없습니다.',
    '대화가 시작된 후에는 주제로 다시 시작할 수 없습니다.',
}


class UserFacingError(ValueError):
    def __init__(self, code):
        if code not in MESSAGES:
            raise ValueError('Unknown public failure code')
        self.code = code
        super().__init__(MESSAGES[code])


def error_code(error):
    if error is None:
        return 'unexpected_error'
    if isinstance(error, str):
        return ALIASES.get(error, error if error in MESSAGES else 'unexpected_error')
    if isinstance(error, UserFacingError):
        return error.code
    if isinstance(error, (DBAPIError, psycopg.Error)):
        return 'storage_failed'
    if isinstance(error, (TimeoutError, httpx.TimeoutException)):
        return 'request_timeout'
    status = getattr(error, 'status_code', None)
    if status == 429:
        return 'rate_limited'
    if status in (401, 403):
        return 'service_authentication'
    if status == 408:
        return 'request_timeout'
    if status in (400, 404, 422):
        return 'service_configuration'
    if isinstance(status, int) and status >= 500:
        return 'service_unavailable'
    if isinstance(error, ValidationError):
        return 'response_invalid'
    if isinstance(error, FileNotFoundError):
        return 'resource_unavailable'
    if isinstance(error, (ConnectionError, httpx.TransportError)):
        return 'connection_failed'
    if str(error) == 'empty_conversation_result':
        return 'response_empty'
    if str(error) in {'NVIDIA_API_KEY is required for LLM_PROVIDER=nvidia', 'OPENAI_API_KEY is required for LLM_PROVIDER=openai'}:
        return 'service_configuration'
    if error.__cause__ is not None:
        code = error_code(error.__cause__)
        if code != 'unexpected_error':
            return code
    code = ALIASES.get(type(error).__name__)
    if code:
        return code
    return 'invalid_input' if isinstance(error, ValueError) else 'unexpected_error'


def failure_message(error):
    return MESSAGES[error_code(error)]


def input_message(error):
    # Only reviewed literal validation text can pass through the API unchanged.
    return str(error) if str(error) in INPUT_MESSAGES else failure_message(error)
