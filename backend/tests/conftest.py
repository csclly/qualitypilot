import os
from pathlib import Path
import shutil
import tempfile

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy.engine import make_url


BACKEND_ROOT = Path(__file__).parents[1]
DEFAULT_TEST_DATABASE_URL = (
    "postgresql+asyncpg://qualitypilot_test:qualitypilot_test_password@"
    "127.0.0.1:5433/qualitypilot_test"
)
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL)
TEST_UPLOAD_DIRECTORY = Path(tempfile.mkdtemp(prefix="qualitypilot-test-uploads-"))


def _require_safe_test_database(database_url: str) -> None:
    database_name = make_url(database_url).database
    if database_name is None or not database_name.endswith("_test"):
        raise RuntimeError(
            "拒绝运行数据库测试：TEST_DATABASE_URL 的数据库名称必须以 _test 结尾"
        )


_require_safe_test_database(TEST_DATABASE_URL)
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["ENVIRONMENT"] = "test"
os.environ["DEBUG"] = "false"
os.environ["UPLOAD_DIRECTORY"] = str(TEST_UPLOAD_DIRECTORY)
# 自动化测试必须显式注入 Fake Provider，禁止意外消耗真实云端额度。
os.environ["DASHSCOPE_API_KEY"] = ""


def _requires_database(request: pytest.FixtureRequest) -> bool:
    return any(
        item.get_closest_marker("integration") is not None
        for item in request.session.items
    )


@pytest.fixture(scope="session", autouse=True)
def prepare_test_environment(request: pytest.FixtureRequest):
    try:
        if _requires_database(request):
            alembic_config = Config(str(BACKEND_ROOT / "alembic.ini"))
            alembic_config.set_main_option(
                "script_location",
                str(BACKEND_ROOT / "alembic"),
            )
            command.upgrade(alembic_config, "head")
        yield
    finally:
        shutil.rmtree(TEST_UPLOAD_DIRECTORY, ignore_errors=True)
