// Official vehicles of Top Speed.
// Names, top speeds, engine frequencies, gear counts and wiper flags come from the remake's
// OfficialVehicleCatalog (TopSpeed.Shared/Vehicles/Catalog/Data.cs). Acceleration, deceleration,
// steering and steering factor come from the original Top Speed 3 CarDefs.h, which drive the
// classic arcade handling model this web port uses. Speeds are in the legacy unit: km/h * 100.
//
// `sounds` lists which files exist in Sounds/Vehicles/VehicleN; anything missing falls back to
// Sounds/Vehicles/default, the same way the remake resolves vehicle sounds.

const v = (id, name, top, legacyTop, accel, decel, idle, topFreq, shift, gears, steering, steeringFactor, wipers, sounds) => ({
    id,
    name,
    folder: `Vehicle${id}`,
    topspeed: top * 100,
    // Legacy acceleration is tuned against the legacy top speed; scale it so cars reach their
    // (newer) top speed in a similar time.
    acceleration: accel * (top * 100) / legacyTop,
    deceleration: decel,
    idlefreq: idle,
    topfreq: topFreq,
    shiftfreq: shift,
    gears,
    steering,
    steeringFactor,
    hasWipers: wipers,
    sounds
});

export const VEHICLES = [
    v(1, 'Nissan GT-R Nismo', 276, 17500, 11, 40, 22050, 55000, 26000, 6, 160, 60, true, ['brake', 'crash', 'engine', 'horn', 'start', 'throttle']),
    v(2, 'Porsche 911 GT3 RS', 286, 18500, 13, 35, 22050, 60000, 35000, 7, 150, 55, true, ['engine', 'horn', 'start', 'throttle']),
    v(3, 'Fiat 500', 136, 15100, 10, 35, 6000, 25000, 19000, 5, 150, 72, true, ['brake', 'crash', 'engine', 'horn']),
    v(4, 'Mini Cooper S', 198, 17200, 12, 40, 6000, 27000, 20000, 6, 140, 56, true, ['engine', 'horn']),
    v(5, 'Ford Mustang 1969', 168, 24000, 12, 60, 6000, 33000, 27500, 4, 230, 80, true, ['engine', 'horn']),
    v(6, 'Toyota Camry', 190, 26000, 9, 90, 7025, 40000, 32500, 8, 220, 95, true, ['brake', 'engine', 'horn']),
    v(7, 'Lamborghini Aventador', 288, 21000, 13, 70, 6000, 26000, 21000, 7, 210, 65, true, ['engine']),
    v(8, 'BMW 3 Series', 230, 23000, 11, 55, 10000, 45000, 34000, 8, 200, 70, true, ['engine']),
    v(9, 'Mercedes Sprinter', 145, 18000, 8, 25, 22050, 30550, 22550, 7, 150, 85, true, ['backfire', 'brake', 'crash', 'engine', 'horn', 'start', 'throttle']),
    v(10, 'Kawasaki Ninja ZX-10R', 216, 20000, 15, 45, 22050, 60000, 35000, 6, 140, 50, false, ['crash', 'engine', 'horn', 'start']),
    v(11, 'Ducati Panigale V4', 242, 22000, 17, 40, 22050, 60000, 35000, 6, 130, 50, false, ['engine', 'start']),
    v(12, 'Yamaha YZF-R1', 219, 24000, 13, 45, 22050, 27550, 23550, 6, 150, 66, false, ['backfire', 'engine', 'horn', 'start', 'throttle'])
];

export function vehicleSoundPath(vehicle, name) {
    if (vehicle.sounds.includes(name))
        return `Vehicles/${vehicle.folder}/${name}.wav`;
    if (name === 'throttle' || name === 'backfire')
        return null; // optional sounds: only vehicles that ship them use them
    return `Vehicles/default/${name}.wav`;
}
