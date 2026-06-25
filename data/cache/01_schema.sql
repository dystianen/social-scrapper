-- ═══════════════════════════════════════════════════
-- Social Sentiment DB Schema
-- Run once on a fresh PostgreSQL database
-- ═══════════════════════════════════════════════════

-- TABLE 1: Top-level content (tweets, IG posts, FB posts, articles)
CREATE TABLE IF NOT EXISTS scraped_items (
  id               SERIAL PRIMARY KEY,
  platform         TEXT NOT NULL,        -- 'twitter' | 'instagram' | 'facebook' | 'news'
  source           TEXT NOT NULL,        -- platform name or news outlet ('detik','kompas',...)
  item_type        TEXT NOT NULL,        -- 'tweet' | 'ig_post' | 'fb_post' | 'article'
  external_id      TEXT,                 -- tweet_id, post_id from source platform
  url              TEXT NOT NULL,
  username         TEXT,
  title            TEXT,                 -- news articles only
  content          TEXT NOT NULL,        -- tweet text / IG caption / FB post / article body
  description      TEXT,                 -- OG meta description (news only)
  author           TEXT,                 -- byline (news only)
  published_at     TIMESTAMPTZ,          -- normalized UTC
  raw_date_str     TEXT,                 -- original date string before normalization
  likes            INTEGER   DEFAULT 0,
  shares           INTEGER   DEFAULT 0,
  comments_count   INTEGER   DEFAULT 0,
  word_count       INTEGER,
  relevance_score  INTEGER   DEFAULT 0,  -- wajib×10 + konteks count
  matched_keywords TEXT[],               -- ['solusiku', 'pinjol', 'ojk', ...]
  extra            JSONB     DEFAULT '{}',
  scraped_at       TIMESTAMPTZ DEFAULT NOW(),
  task_id          TEXT,
  UNIQUE(platform, url)
);

-- TABLE 2: Comments (child rows linked to scraped_items)
CREATE TABLE IF NOT EXISTS comments (
  id               SERIAL PRIMARY KEY,
  item_id          INTEGER NOT NULL REFERENCES scraped_items(id) ON DELETE CASCADE,
  platform         TEXT NOT NULL,
  username         TEXT,
  text             TEXT NOT NULL,
  published_at     TIMESTAMPTZ,
  raw_date_str     TEXT,
  likes            INTEGER DEFAULT 0,
  reply_to_id      INTEGER REFERENCES comments(id) ON DELETE SET NULL,
  extra            JSONB   DEFAULT '{}',
  scraped_at       TIMESTAMPTZ DEFAULT NOW()
);

-- TABLE 3: Sentiment scores — applies to BOTH scraped_items and comments
CREATE TABLE IF NOT EXISTS sentiment_scores (
  id               SERIAL PRIMARY KEY,
  item_id          INTEGER REFERENCES scraped_items(id) ON DELETE CASCADE,
  comment_id       INTEGER REFERENCES comments(id)      ON DELETE CASCADE,
  sentiment        TEXT  NOT NULL CHECK (sentiment IN ('positive','negative','neutral')),
  score            FLOAT,               -- -1.0 to 1.0
  confidence       FLOAT,               -- 0.0 to 1.0
  model            TEXT  DEFAULT 'deepseek-chat',
  raw_response     TEXT,                -- DeepSeek raw JSON output for audit
  analyzed_at      TIMESTAMPTZ DEFAULT NOW(),
  CONSTRAINT chk_exactly_one_target CHECK (
    (item_id IS NOT NULL)::INT + (comment_id IS NOT NULL)::INT = 1
  )
);

-- TABLE 4: Scraping run audit log
CREATE TABLE IF NOT EXISTS scraping_runs (
  id                SERIAL PRIMARY KEY,
  platform          TEXT NOT NULL,
  task_type         TEXT,
  source            TEXT,
  status            TEXT DEFAULT 'success' CHECK (status IN ('success','failed','partial')),
  items_scraped     INTEGER DEFAULT 0,
  comments_scraped  INTEGER DEFAULT 0,
  error_message     TEXT,
  started_at        TIMESTAMPTZ,
  finished_at       TIMESTAMPTZ DEFAULT NOW()
);

-- ═══════════════════════════════════════════════════
-- INDEXES
-- ═══════════════════════════════════════════════════
CREATE INDEX IF NOT EXISTS idx_items_platform   ON scraped_items(platform);
CREATE INDEX IF NOT EXISTS idx_items_source     ON scraped_items(source);
CREATE INDEX IF NOT EXISTS idx_items_published  ON scraped_items(published_at DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_items_scraped    ON scraped_items(scraped_at DESC);
CREATE INDEX IF NOT EXISTS idx_items_type       ON scraped_items(item_type);

CREATE INDEX IF NOT EXISTS idx_comments_item    ON comments(item_id);
CREATE INDEX IF NOT EXISTS idx_comments_platform ON comments(platform);

CREATE INDEX IF NOT EXISTS idx_sentiment_item   ON sentiment_scores(item_id);
CREATE INDEX IF NOT EXISTS idx_sentiment_comment ON sentiment_scores(comment_id);
CREATE INDEX IF NOT EXISTS idx_sentiment_type   ON sentiment_scores(sentiment);
CREATE INDEX IF NOT EXISTS idx_sentiment_date   ON sentiment_scores(analyzed_at DESC);
