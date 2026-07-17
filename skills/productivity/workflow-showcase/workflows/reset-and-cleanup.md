# Reset and cleanup

`PRODUCT_CLI workflow showcase reset ID --json` removes only ownership-tagged temporary staging. It reports an exact owned schedule but never deletes it. Route schedule removal through the ordinary explicitly confirmed cron command.

`PRODUCT_CLI workflow showcase cleanup --json` is a dry run. Only use `--execute` after explicit confirmation; retained audit evidence is otherwise preserved.
