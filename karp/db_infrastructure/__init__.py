from sqlalchemy import create_engine, orm, pool


class Database:
    def __init__(self, db_url: str) -> None:
        kwargs = {}
        if str(db_url).startswith("sqlite"):
            kwargs["poolclass"] = pool.SingletonThreadPool
        self._engine = create_engine(db_url, echo=True, **kwargs)
        self.session_factory = orm.sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=self._engine,
        )

#    @contextmanager
#    def session(self) -> Callable[..., AbstractContextManager[Session]]:
#        session: Session = self._session_factory()
#        try:
#            yield session
#        except Exception:
#            logger.exception('Session rollback because of exception')
#            session.rollback()
#            raise
#        finally:
#            session.close()
