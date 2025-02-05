import os

import socketio
from dotenv import load_dotenv

from openhands.core.config import load_app_config
from openhands.server.config.server_config import load_server_config
from openhands.server.conversation_manager.conversation_manager import (
    ConversationManager,
)
from openhands.storage import get_file_store
from openhands.storage.conversation.conversation_store import ConversationStore
from openhands.utils.import_utils import get_impl

load_dotenv()

config = load_app_config()
server_config = load_server_config()
file_store = get_file_store(config.file_store, config.file_store_path)

client_manager = None
redis_host = os.environ.get('REDIS_HOST')
if redis_host:
    client_manager = socketio.AsyncRedisManager(
        f'redis://{redis_host}',
        redis_options={'password': os.environ.get('REDIS_PASSWORD')},
    )


sio = socketio.AsyncServer(
    async_mode='asgi', cors_allowed_origins='*', client_manager=client_manager
)

ConversationManagerImpl = get_impl(
    ConversationManager,  # type: ignore
    server_config.conversation_manager_class,
)
conversation_manager = ConversationManagerImpl.get_instance(sio, config, file_store)

ConversationStoreImpl = get_impl(
    ConversationStore,  # type: ignore
    server_config.conversation_store_class,
)
