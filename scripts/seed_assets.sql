INSERT INTO assets
(name, asset_type, layer, status, latitude, longitude, underground, source, notes, created_at)
VALUES
('Telephone cabinet T-001', 'telephone_cabinet', 'TELECOM', 'ACTIVE', 33.201, 35.779, false, 'manual', 'Pilot record', NOW()),
('Electricity pole E-001', 'electricity_pole', 'ELECTRICITY', 'ACTIVE', 33.202, 35.780, false, 'manual', 'Pilot record', NOW()),
('Water valve W-001', 'water_valve', 'WATER', 'ACTIVE', 33.203, 35.781, true, 'engineering_import', 'Underground asset', NOW()),
('Sewage manhole S-001', 'sewage_manhole', 'SEWAGE', 'ACTIVE', 33.204, 35.782, false, 'manual', 'Visible cover, underground network', NOW()),
('Utility conduit C-001', 'utility_conduit', 'TUNNEL', 'ACTIVE', NULL, NULL, true, 'engineering_import', 'Geometry should be imported as line/polygon in GIS phase', NOW());
