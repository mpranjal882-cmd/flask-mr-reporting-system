-- database.sql
CREATE DATABASE IF NOT EXISTS mr_reporting;
USE mr_reporting;

-- users table: role = 'admin' or 'mr'
CREATE TABLE IF NOT EXISTS users (
  id INT AUTO_INCREMENT PRIMARY KEY,
  username VARCHAR(100) NOT NULL UNIQUE,
  password VARCHAR(255) NOT NULL,
  full_name VARCHAR(150) DEFAULT NULL,
  role ENUM('admin','mr') NOT NULL DEFAULT 'mr',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- reports table
CREATE TABLE IF NOT EXISTS reports (
  id INT AUTO_INCREMENT PRIMARY KEY,
  mr_id INT NOT NULL,
  doctor_name VARCHAR(255) NOT NULL,
  hospital_name VARCHAR(255) NOT NULL,
  location VARCHAR(255),
  visit_date DATE NOT NULL,
  products_promoted TEXT,
  remarks TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (mr_id) REFERENCES users(id) ON DELETE CASCADE
);

-- sample admin user (note: password is plaintext 'admin123' here).
-- It's better to create admin through the app or replace password with a hashed one.
INSERT INTO users (username, password, full_name, role) VALUES
('admin', 'admin123', 'Admin User', 'admin');

-- optional sample MR (password 'mr123')
INSERT INTO users (username, password, full_name, role) VALUES
('mr1', 'mr123', 'Ravi Kumar', 'mr');