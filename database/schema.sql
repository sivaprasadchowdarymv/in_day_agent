-- BharatTrade AI schema (generated from database/models.py; SQLite dialect).
-- PostgreSQL: set DATABASE_URL and the app creates the equivalent tables automatically.

CREATE TABLE audit_logs (
	id INTEGER NOT NULL, 
	created_at DATETIME NOT NULL, 
	actor VARCHAR(40) NOT NULL, 
	action VARCHAR(80) NOT NULL, 
	details JSON NOT NULL, 
	PRIMARY KEY (id)
);
CREATE INDEX ix_audit_logs_created_at ON audit_logs (created_at);

CREATE TABLE backtest_runs (
	id INTEGER NOT NULL, 
	created_at DATETIME NOT NULL, 
	strategies JSON NOT NULL, 
	symbols JSON NOT NULL, 
	interval VARCHAR(8) NOT NULL, 
	data_label VARCHAR(16) NOT NULL, 
	params JSON NOT NULL, 
	metrics JSON NOT NULL, 
	trades JSON NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE instruments (
	symbol VARCHAR(32) NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	sector VARCHAR(40) NOT NULL, 
	series VARCHAR(8) NOT NULL, 
	isin VARCHAR(20), 
	source VARCHAR(20) NOT NULL, 
	verified BOOLEAN NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (symbol)
);

CREATE TABLE market_snapshots (
	id INTEGER NOT NULL, 
	created_at DATETIME NOT NULL, 
	regime VARCHAR(40) NOT NULL, 
	data_label VARCHAR(16) NOT NULL, 
	payload JSON NOT NULL, 
	PRIMARY KEY (id)
);
CREATE INDEX ix_market_snapshots_created_at ON market_snapshots (created_at);

CREATE TABLE scan_runs (
	id INTEGER NOT NULL, 
	started_at DATETIME NOT NULL, 
	finished_at DATETIME, 
	status VARCHAR(20) NOT NULL, 
	data_provider VARCHAR(60) NOT NULL, 
	data_label VARCHAR(16) NOT NULL, 
	universe_size INTEGER NOT NULL, 
	scanned INTEGER NOT NULL, 
	eligible INTEGER NOT NULL, 
	candidates INTEGER NOT NULL, 
	recommendations INTEGER NOT NULL, 
	regime VARCHAR(40), 
	coverage_note TEXT, 
	state_log JSON NOT NULL, 
	settings_snapshot JSON NOT NULL, 
	PRIMARY KEY (id)
);
CREATE INDEX ix_scan_runs_started_at ON scan_runs (started_at);

CREATE TABLE schema_version (
	version INTEGER NOT NULL, 
	applied_at DATETIME NOT NULL, 
	PRIMARY KEY (version)
);

CREATE TABLE screenshots (
	id INTEGER NOT NULL, 
	session_id VARCHAR(64) NOT NULL, 
	filename VARCHAR(255) NOT NULL, 
	sha256 VARCHAR(64) NOT NULL, 
	width INTEGER NOT NULL, 
	height INTEGER NOT NULL, 
	size_bytes INTEGER NOT NULL, 
	stored_path VARCHAR(400) NOT NULL, 
	uploaded_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_screenshot_session_hash UNIQUE (session_id, sha256)
);
CREATE INDEX ix_screenshots_sha256 ON screenshots (sha256);
CREATE INDEX ix_screenshots_session_id ON screenshots (session_id);

CREATE TABLE user_settings (
	"key" VARCHAR(80) NOT NULL, 
	value JSON NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY ("key")
);

CREATE TABLE candidate_rankings (
	id INTEGER NOT NULL, 
	scan_run_id INTEGER NOT NULL, 
	symbol VARCHAR(32) NOT NULL, 
	origin VARCHAR(20) NOT NULL, 
	strategy VARCHAR(40) NOT NULL, 
	direction VARCHAR(8) NOT NULL, 
	score FLOAT NOT NULL, 
	passed BOOLEAN NOT NULL, 
	rejection_reasons JSON NOT NULL, 
	components JSON NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(scan_run_id) REFERENCES scan_runs (id)
);
CREATE INDEX ix_candidate_rankings_scan_run_id ON candidate_rankings (scan_run_id);

CREATE TABLE image_evidence (
	id INTEGER NOT NULL, 
	screenshot_id INTEGER NOT NULL, 
	evidence JSON NOT NULL, 
	verification JSON NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(screenshot_id) REFERENCES screenshots (id)
);
CREATE INDEX ix_image_evidence_screenshot_id ON image_evidence (screenshot_id);

CREATE TABLE indicator_snapshots (
	id INTEGER NOT NULL, 
	scan_run_id INTEGER, 
	symbol VARCHAR(32) NOT NULL, 
	interval VARCHAR(8) NOT NULL, 
	candle_ts DATETIME NOT NULL, 
	"values" JSON NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_ind_snapshot UNIQUE (scan_run_id, symbol, interval), 
	FOREIGN KEY(scan_run_id) REFERENCES scan_runs (id)
);
CREATE INDEX ix_indicator_snapshots_symbol ON indicator_snapshots (symbol);
CREATE INDEX ix_indicator_snapshots_scan_run_id ON indicator_snapshots (scan_run_id);

CREATE TABLE recommendations (
	id INTEGER NOT NULL, 
	scan_run_id INTEGER NOT NULL, 
	rank INTEGER NOT NULL, 
	symbol VARCHAR(32) NOT NULL, 
	direction VARCHAR(8) NOT NULL, 
	strategy VARCHAR(40) NOT NULL, 
	status VARCHAR(24) NOT NULL, 
	score FLOAT NOT NULL, 
	"plan" JSON NOT NULL, 
	explanation JSON NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(scan_run_id) REFERENCES scan_runs (id)
);
CREATE INDEX ix_recommendations_scan_run_id ON recommendations (scan_run_id);
CREATE INDEX ix_recommendations_symbol ON recommendations (symbol);

CREATE TABLE paper_trades (
	id INTEGER NOT NULL, 
	recommendation_id INTEGER, 
	symbol VARCHAR(32) NOT NULL, 
	direction VARCHAR(8) NOT NULL, 
	strategy VARCHAR(40) NOT NULL, 
	sector VARCHAR(40) NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	quantity INTEGER NOT NULL, 
	open_quantity INTEGER NOT NULL, 
	entry_trigger FLOAT NOT NULL, 
	entry_limit FLOAT NOT NULL, 
	stop_loss FLOAT NOT NULL, 
	initial_stop FLOAT NOT NULL, 
	target1 FLOAT NOT NULL, 
	target2 FLOAT, 
	entry_price FLOAT, 
	exit_price FLOAT, 
	realized_gross FLOAT NOT NULL, 
	costs FLOAT NOT NULL, 
	realized_net FLOAT NOT NULL, 
	exit_reason VARCHAR(40), 
	data_label VARCHAR(16) NOT NULL, 
	created_at DATETIME NOT NULL, 
	entry_at DATETIME, 
	exit_at DATETIME, 
	events JSON NOT NULL, 
	exits JSON NOT NULL, 
	t1_done BOOLEAN NOT NULL, 
	valid_until DATETIME, 
	last_processed DATETIME, 
	PRIMARY KEY (id), 
	FOREIGN KEY(recommendation_id) REFERENCES recommendations (id)
);
CREATE INDEX ix_paper_trades_status ON paper_trades (status);
CREATE INDEX ix_paper_trades_symbol ON paper_trades (symbol);
CREATE INDEX ix_paper_trades_created_at ON paper_trades (created_at);

CREATE TABLE trade_journal (
	id INTEGER NOT NULL, 
	paper_trade_id INTEGER, 
	created_at DATETIME NOT NULL, 
	kind VARCHAR(20) NOT NULL, 
	text TEXT NOT NULL, 
	tags JSON NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(paper_trade_id) REFERENCES paper_trades (id)
);
CREATE INDEX ix_trade_journal_paper_trade_id ON trade_journal (paper_trade_id);

-- Candle cache (data/cache.py, separate file storage/candle_cache.sqlite):
-- CREATE TABLE candles (symbol TEXT, interval TEXT, ts INTEGER, open REAL, high REAL, low REAL, close REAL, volume REAL, PRIMARY KEY (symbol, interval, ts));