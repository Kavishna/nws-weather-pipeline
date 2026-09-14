"""
Single-pass entrypoint for a cron-triggered free host (GitHub Actions here,
but this works identically on any scheduler that just runs a script and
exits - Render Cron Jobs, a home machine's crontab, etc.). Runs exactly one
pipeline pass and exits - the *scheduler* provides the "every N minutes"
loop now, not a `while True` inside this process.
"""
import sys
import nws_mapbox_generator as pipeline


def main():
    db = pipeline.get_firestore_client()
    try:
        pipeline.run_alert_pipeline(db, states=pipeline.TARGET_STATES)
    except Exception as e:
        print(f"⚠️ Pipeline run failed: {e}")
        sys.exit(1)  # non-zero exit -> the scheduler (GitHub Actions) marks this run as failed


if __name__ == "__main__":
    main()
