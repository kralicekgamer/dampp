<?php
// dampp landing page - feel free to delete it or replace it with your own index.php
if (isset($_GET['phpinfo'])) {
    phpinfo();
    exit;
}

$host = explode(':', $_SERVER['HTTP_HOST'] ?? 'localhost')[0];
$pmaUrl = "http://$host:" . (getenv('PMA_PORT') ?: '8080');
$dbPort = getenv('MARIADB_PORT') ?: '3306';

// database status
$dbOk = false;
if (!extension_loaded('mysqli')) {
    $dbInfo = 'the mysqli extension is still installing – try again in a moment';
} else {
    mysqli_report(MYSQLI_REPORT_OFF);
    $db = mysqli_init();
    $db->options(MYSQLI_OPT_CONNECT_TIMEOUT, 2);
    if (@$db->real_connect('mariadb', 'root', getenv('MARIADB_ROOT_PASSWORD') ?: 'root')) {
        $dbOk = true;
        $dbInfo = $db->server_info;
        $db->close();
    } else {
        $dbInfo = 'not running or login failed';
    }
}

// is phpMyAdmin up? (reachable from the php container as host "phpmyadmin")
$pma = @fsockopen('phpmyadmin', 80, $errno, $errstr, 1);
$pmaOk = (bool) $pma;
if ($pma) fclose($pma);

// folders in the web root = projects
$projects = [];
foreach (scandir(__DIR__) as $entry) {
    if ($entry[0] !== '.' && is_dir(__DIR__ . '/' . $entry)) $projects[] = $entry;
}
$e = fn(string $s): string => htmlspecialchars($s, ENT_QUOTES);
?>
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>dampp</title>
<style>
  :root {
    color-scheme: light dark;
    --bg: #f5f5f2; --card: #fff; --text: #1c1c1a; --muted: #6b6b66; --line: #e2e2dc;
    --accent: #e8590c; --ok: #2b8a3e; --off: #c92a2a;
  }
  @media (prefers-color-scheme: dark) {
    :root { --bg: #161615; --card: #20201e; --text: #ecece8; --muted: #9a9a93; --line: #33332f; --accent: #ff8a3d; --ok: #51cf66; --off: #ff6b6b; }
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--text); font: 16px/1.5 system-ui, sans-serif; }
  main { max-width: 760px; margin: 0 auto; padding: 56px 16px; }
  h1 { margin: 0; font-size: 40px; letter-spacing: -1px; }
  h1 span { color: var(--accent); }
  .lead { margin: 4px 0 32px; color: var(--muted); }
  h2 { margin: 32px 0 12px; font-size: 13px; text-transform: uppercase; letter-spacing: 1px; color: var(--muted); }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; }
  .card { display: block; padding: 16px; background: var(--card); border: 1px solid var(--line); border-radius: 10px; color: inherit; text-decoration: none; }
  a.card:hover { border-color: var(--accent); }
  .card b { display: block; }
  .card small { color: var(--muted); overflow-wrap: anywhere; }
  .dot { display: inline-block; width: 8px; height: 8px; margin-right: 6px; border-radius: 50%; background: var(--off); }
  .dot.ok { background: var(--ok); }
  code { font-size: 14px; }
</style>
</head>
<body>
<main>
  <h1>d<span>ampp</span></h1>
  <p class="lead">Local nginx, PHP and MariaDB in containers.</p>

  <h2>Status</h2>
  <div class="grid">
    <div class="card"><b><span class="dot ok"></span>nginx</b><small><?= $e($_SERVER['SERVER_SOFTWARE'] ?? '') ?></small></div>
    <div class="card"><b><span class="dot ok"></span>PHP</b><small><?= $e(PHP_VERSION) ?></small></div>
    <div class="card"><b><span class="dot <?= $dbOk ? 'ok' : '' ?>"></span>MariaDB</b><small><?= $e($dbInfo) ?></small></div>
  </div>

  <h2>Tools</h2>
  <div class="grid">
    <a class="card" href="<?= $e($pmaUrl) ?>"><b><span class="dot <?= $pmaOk ? 'ok' : '' ?>"></span>phpMyAdmin</b><small><?= $pmaOk ? 'database administration' : 'not running – start it in dampp' ?></small></a>
    <a class="card" href="?phpinfo"><b>phpinfo()</b><small>PHP configuration and extensions</small></a>
  </div>

  <h2>Projects</h2>
  <div class="grid">
    <?php foreach ($projects as $p): ?>
      <a class="card" href="<?= $e(rawurlencode($p)) ?>/"><b><?= $e($p) ?></b><small>/<?= $e($p) ?>/</small></a>
    <?php endforeach; ?>
    <?php if (!$projects): ?>
      <div class="card"><b>Nothing yet</b><small>Create a folder in the web root and it will show up here.</small></div>
    <?php endif; ?>
  </div>

  <h2>Database connection</h2>
  <div class="card">
    <small>From PHP: host <code>mariadb</code>, port <code>3306</code>, user <code>root</code>.<br>
    From your computer (DBeaver, the <code>mariadb</code> client…): <code>127.0.0.1:<?= $e($dbPort) ?></code>. The database port is not a website, it will not open in a browser.</small>
  </div>
</main>
</body>
</html>
