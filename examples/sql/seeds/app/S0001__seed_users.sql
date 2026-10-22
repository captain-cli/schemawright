INSERT INTO users(username)
SELECT 'admin'
WHERE NOT EXISTS (SELECT 1 FROM users WHERE username = 'admin');
