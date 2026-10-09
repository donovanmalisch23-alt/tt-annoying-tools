// Player car and computer players, ported from the original Car.cpp and ComputerPlayer.cpp.
// Units: positions in 1/100 metre, speed in 1/100 km/h (so topspeed 27600 = 276 km/h).

import { vehicleSoundPath } from './data-vehicles.js';

const MAX_SURFACE_FREQ = 100000;
// The original moved cars one unit (1 cm) per second for each unit of speed, which made a car
// travel 3.6 times faster than its speedometer said. The remake drives at real speed; scale both
// forward and sideways movement so the handling keeps its original feel at real-world pace.
export const TIME_SCALE = 1 / 3.6;
const SURFACES = ['Asphalt', 'Gravel', 'Water', 'Sand', 'Snow'];

export const State = { Stopped: 0, Starting: 1, Running: 2, Crashing: 3, Stopping: 4 };

const rnd = n => Math.floor(Math.random() * n);

function surfaceFactors(surface, accel, decel, bot = false) {
    switch (surface) {
        case 'Gravel': return [accel * 2 / 3, decel * 2 / 3];
        case 'Water': return [accel * 3 / 5, decel * 3 / 5];
        case 'Sand': return bot ? [accel * 3 / 8, decel * 5 / 4] : [accel / 2, decel * 3 / 2];
        case 'Snow': return [accel, decel / 2];
        default: return [accel, decel];
    }
}

// Shared bits of both car kinds: timed events and the automatic-gearbox engine pitch.
class Vehicle {
    constructor(race, vehicle) {
        this.race = race;
        this.v = vehicle;
        this.topspeed = vehicle.topspeed;
        this.acceleration = vehicle.acceleration;
        this.deceleration = vehicle.deceleration;
        this.idlefreq = vehicle.idlefreq;
        this.topfreq = vehicle.topfreq;
        this.shiftfreq = vehicle.shiftfreq;
        this.gears = vehicle.gears;
        this.steering = vehicle.steering;
        this.steeringFactor = vehicle.steeringFactor;
        this.state = State.Stopped;
        this.speed = 0;
        this.gear = 1;
        this.x = 0;
        this.y = 0;
        this.surface = 'Asphalt';
        this.frequency = this.idlefreq;
        this.events = [];
        this.finished = false;
    }

    push(type, delay) {
        this.events.push({ type, time: this.race.time + delay });
    }

    takeEvents() {
        const due = this.events.filter(e => e.time <= this.race.time);
        this.events = this.events.filter(e => e.time > this.race.time);
        return due;
    }

    autoEngineFrequency() {
        const gearRange = this.topspeed / (this.gears + 1);
        let freq;
        let shifting = false;
        if (this.speed / gearRange < 2) {
            const gearSpeed = this.speed / (2 * gearRange);
            freq = gearSpeed * (this.topfreq - this.idlefreq) + this.idlefreq;
            this.gear = 1;
        } else {
            this.gear = Math.min(this.gears, Math.floor(this.speed / gearRange));
            const gearSpeed = (this.speed - this.gear * gearRange) / gearRange;
            if (gearSpeed < 0.07) {
                freq = ((0.07 - gearSpeed) / 0.07) * (this.topfreq - this.shiftfreq) + this.shiftfreq;
                shifting = true;
            } else {
                freq = gearSpeed * (this.topfreq - this.shiftfreq) + this.shiftfreq;
            }
        }
        this.frequency = freq;
        return shifting;
    }
}

export class PlayerCar extends Vehicle {
    constructor(race, vehicle, manual) {
        super(race, vehicle);
        this.manual = manual;
        this.switchingGear = 0;
        this.throttleVolume = 0;
        this.thrust = 0;
        this.backfirePlayed = false;
        this.backfirePlayedAuto = false;
        this.stickReleased = true;
        this.relPos = 0.5;
        this.laneWidth = race.track.laneWidth * 2;
        this.currentSteering = 0;
    }

    soundPaths() {
        const v = this.v;
        return [
            ...['engine', 'start', 'horn', 'throttle', 'crash', 'brake', 'backfire'].map(n => vehicleSoundPath(v, n)),
            'Legacy/bump.wav', 'Legacy/crashshort.wav', 'Legacy/badswitch.wav', 'Legacy/wipers.wav',
            ...SURFACES.map(s => `Legacy/${s.toLowerCase()}.wav`)
        ];
    }

    createSounds() {
        const a = this.race.audio;
        const v = this.v;
        const p = n => vehicleSoundPath(v, n);
        this.sEngine = a.create(p('engine'), 'engine');
        this.sStart = a.create(p('start'), 'engine');
        this.sThrottle = p('throttle') ? a.create(p('throttle'), 'engine') : null;
        this.sHorn = a.create(p('horn'), 'events');
        this.sCrash = a.create(p('crash'), 'events');
        this.sBrake = a.create(p('brake'), 'events');
        this.sBackfire = p('backfire') ? a.create(p('backfire'), 'events') : null;
        this.sBump = a.create('Legacy/bump.wav', 'events');
        this.sMiniCrash = a.create('Legacy/crashshort.wav', 'events');
        this.sBadSwitch = a.create('Legacy/badswitch.wav', 'events');
        this.hasWipers = v.hasWipers && (this.race.track.weather === 'Rain' || this.race.track.weather === 'Storm');
        this.sWipers = this.hasWipers ? a.create('Legacy/wipers.wav', 'events') : null;
        this.sSurface = {};
        for (const s of SURFACES) {
            this.sSurface[s] = a.create(`Legacy/${s.toLowerCase()}.wav`, 'surface');
            this.sSurface[s].volume(90);
        }
        this.surface = this.race.track.segments[0].surface;
        this.loops = [this.sEngine, this.sThrottle, this.sHorn, this.sBrake, this.sWipers, ...Object.values(this.sSurface)].filter(Boolean);
    }

    allSounds() {
        return [...this.loops, this.sStart, this.sCrash, this.sBackfire, this.sBump, this.sMiniCrash, this.sBadSwitch].filter(Boolean);
    }

    start() {
        this.push('carStart', Math.max(0.05, this.sStart.length - 0.1));
        this.sStart.play();
        this.speed = 0;
        this.frequency = this.idlefreq;
        this.switchingGear = 0;
        this.throttleVolume = 0;
        this.gear = 1;
        for (const s of Object.values(this.sSurface)) s.frequency(0);
        this.state = State.Starting;
    }

    crash() {
        this.speed = 0;
        this.throttleVolume = 0;
        this.sCrash.play();
        for (const s of this.loops) { s.stop(); s.pan(0); }
        this.sSurface[this.surface].volume(90);
        this.gear = 1;
        this.switchingGear = 0;
        this.state = State.Crashing;
        this.push('carRestart', this.sCrash.length + 1.25);
        this.race.vibrate([80, 40, 200]);
        this.race.onPlayerCrash();
    }

    miniCrash(newX) {
        this.speed /= 4;
        this.x = newX;
        this.throttleVolume = 0;
        this.sMiniCrash.play();
        this.race.vibrate(60);
    }

    bump(bumpX, bumpY, bumpSpeed) {
        if (bumpY !== 0) {
            this.speed -= bumpSpeed;
            this.y += bumpY;
        }
        if (bumpX !== 0) {
            this.x += 2 * bumpX;
            this.speed -= this.speed / 5;
        }
        if (this.speed < 0) this.speed = 0;
        this.sBump.play();
        this.race.vibrate(40);
    }

    stop() {
        this.sBrake.stop();
        if (this.sWipers) this.sWipers.stop();
        this.state = State.Stopping;
    }

    quiet() {
        this.sBrake.stop();
        this.sHorn.stop();
        for (const s of Object.values(this.sSurface)) s.volume(0);
    }

    run(dt, input) {
        const horning = input.horn;
        if (this.state === State.Running && this.race.started) {
            this.currentSteering = input.steering;
            const throttle = input.clutch ? 0 : input.throttle;
            const brake = input.brake;
            let [accel, decel] = surfaceFactors(this.surface, this.acceleration, this.deceleration);
            let factor1 = 100;
            if (this.manual) {
                if (!input.gearUp && !input.gearDown) this.stickReleased = true;
                factor1 = this.manualAcceleration();
                if (input.gearDown && this.gear > 1 && this.stickReleased) {
                    this.stickReleased = false;
                    this.switchingGear = -1;
                    this.gear--;
                    if (!input.clutch && this.frequency > 3 * this.topfreq / 2) this.sBadSwitch.play();
                    this.maybeBackfire();
                    this.push('inGear', 0.2);
                    this.race.announceGear(this.gear);
                } else if (input.gearUp && this.gear < this.gears && this.stickReleased) {
                    this.stickReleased = false;
                    this.switchingGear = 1;
                    this.gear++;
                    if (!input.clutch && this.frequency < this.idlefreq) this.sBadSwitch.play();
                    this.maybeBackfire();
                    this.push('inGear', 0.2);
                    this.race.announceGear(this.gear);
                }
            }
            this.updateThrottleSound(dt, throttle);
            let thrust = throttle;
            if (throttle === 0) thrust = brake;
            else if (brake !== 0 && -brake > throttle) thrust = brake;
            this.thrust = thrust;

            let factor2 = 1;
            if (this.currentSteering !== 0 && this.speed > this.topspeed / 2)
                factor2 = 1 - (1.5 * this.speed / this.topspeed) * Math.abs(this.currentSteering) / 100;

            let speedDiff;
            if (thrust > 10) {
                speedDiff = dt * thrust * accel * factor1 * factor2 / 100;
                this.backfirePlayed = false;
            } else if (thrust < -10) {
                speedDiff = dt * thrust * decel;
            } else {
                speedDiff = dt * -1000;
            }
            if (speedDiff > 0) speedDiff *= 2 - (this.topspeed + this.speed) / (2 * this.topspeed);
            this.speed = Math.max(0, Math.min(this.topspeed, this.speed + speedDiff));

            if (thrust <= 0 && this.sBackfire) {
                if (!this.sBackfire.playing && !this.backfirePlayed && rnd(5) === 1) this.sBackfire.play();
                this.backfirePlayed = true;
            }

            if (thrust < -50 && this.speed > 0) {
                this.brakeSound();
                this.currentSteering = this.currentSteering * 2 / 3;
            } else if (this.currentSteering !== 0 && this.speed > this.topspeed / 2) {
                if (thrust > -50) this.brakeCurveSound();
            } else {
                if (this.sBrake.playing) this.sBrake.stop();
                for (const s of Object.values(this.sSurface)) s.volume(90);
            }

            this.y += this.speed * dt * TIME_SCALE;
            const steerMul = this.surface === 'Snow' ? this.steering * 1.44 : this.steering;
            this.x += this.currentSteering * dt * TIME_SCALE * steerMul * ((5000 + this.speed * this.steeringFactor / 100) / this.topspeed);

            this.sBrake.frequency(11025 + 22050 * this.speed / this.topspeed);
            this.sBrake.volume(this.speed <= 5000 ? 100 - (50 - this.speed / 100) : 100);
            if (this.manual) this.manualEngineFrequency();
            else if (this.autoEngineFrequency() && this.sBackfire) {
                if (!this.backfirePlayedAuto && rnd(5) === 1 && !this.sBackfire.playing) this.sBackfire.play();
                this.backfirePlayedAuto = true;
            } else this.backfirePlayedAuto = false;
            this.applyEngineFrequency();
            this.updateSurfaceSound();
            const surf = this.sSurface[this.surface];
            if (surf && !surf.playing) surf.play(true);
        } else if (this.state === State.Stopping) {
            this.speed = Math.max(0, this.speed - dt * 100 * this.deceleration);
            this.autoEngineFrequency();
            this.applyEngineFrequency();
            this.updateSurfaceSound();
        }

        if (horning && this.state !== State.Stopped && this.state !== State.Crashing) {
            if (!this.sHorn.playing) this.sHorn.play(true);
        } else if (this.sHorn.playing) {
            this.sHorn.stop();
        }

        for (const e of this.takeEvents()) {
            if (e.type === 'carStart') {
                this.frequency = this.idlefreq;
                this.applyEngineFrequency();
                this.sEngine.play(true);
                if (this.sWipers) this.sWipers.play(true);
                this.state = State.Running;
            } else if (e.type === 'carRestart') {
                this.start();
            } else if (e.type === 'inGear') {
                this.switchingGear = 0;
            }
        }
    }

    maybeBackfire() {
        if (this.sBackfire && !this.sBackfire.playing && rnd(5) === 1) this.sBackfire.play();
    }

    updateThrottleSound(dt, throttle) {
        if (!this.sThrottle) return;
        if (this.sEngine.playing) {
            if (throttle > 50) {
                if (!this.sThrottle.playing) {
                    this.sThrottle.volume(this.throttleVolume);
                    this.sThrottle.play(true);
                } else {
                    if (this.throttleVolume >= 80) this.throttleVolume += (100 - this.throttleVolume) * dt;
                    else this.throttleVolume = 80;
                    this.throttleVolume = Math.min(100, this.throttleVolume);
                    this.sThrottle.volume(this.throttleVolume);
                }
            } else {
                this.throttleVolume -= 10 * dt;
                const floor = this.speed * 95 / this.topspeed;
                if (this.throttleVolume < floor) this.throttleVolume = floor;
                this.sThrottle.volume(this.throttleVolume);
            }
        } else if (this.sThrottle.playing) {
            this.sThrottle.stop();
        }
    }

    brakeSound() {
        if (this.surface === 'Asphalt') {
            if (!this.sBrake.playing) {
                this.sSurface.Asphalt.volume(90);
                this.sBrake.play(true);
            }
        } else {
            if (this.sBrake.playing) this.sBrake.stop();
            this.sSurface[this.surface].volume(this.speed <= 5000 ? 100 - (10 - this.speed / 500) : 100);
        }
    }

    brakeCurveSound() {
        if (this.sBrake.playing) this.sBrake.stop();
        this.sSurface[this.surface].volume(92 * Math.abs(this.currentSteering) / 100);
    }

    manualAcceleration() {
        const gearSpeed = this.topspeed / this.gears;
        const gearCenter = gearSpeed * (this.gear - 0.82);
        const rel = (this.speed - gearCenter) / gearSpeed;
        const acc = Math.abs(rel) < 1.9
            ? 100 * (0.5 + Math.cos(rel * Math.PI * 0.5))
            : 100 * (0.5 + Math.cos(0.95 * Math.PI));
        return Math.max(5, acc);
    }

    manualEngineFrequency() {
        const gearRange = this.topspeed / this.gears;
        const prev = this.frequency;
        let f;
        if (this.gear === 1) {
            f = this.speed < (4 / 3) * gearRange
                ? this.idlefreq + (this.speed * 3 / (2 * gearRange)) * (this.topfreq - this.idlefreq)
                : this.idlefreq + 2 * (this.topfreq - this.idlefreq);
        } else {
            const shiftPoint = (2 / 3 + (this.gear - 1)) * gearRange;
            f = (this.speed / shiftPoint) * this.topfreq;
            f = Math.max(this.idlefreq / 2, Math.min(2 * this.topfreq, f));
        }
        if (this.switchingGear !== 0) f = (2 * prev + f) / 3;
        this.frequency = f;
    }

    applyEngineFrequency() {
        this.sEngine.frequency(this.frequency);
        if (this.sThrottle) this.sThrottle.frequency(this.frequency);
    }

    updateSurfaceSound() {
        const f = Math.min(MAX_SURFACE_FREQ, this.speed * 5);
        const s = this.sSurface[this.surface];
        if (s) s.frequency(this.surface === 'Sand' ? f / 2.5 : f);
    }

    evaluate(road) {
        if (this.state === State.Running && this.race.started) {
            if (road.surface !== this.surface) {
                this.sSurface[this.surface].stop();
                this.surface = road.surface;
                this.updateSurfaceSound();
                this.sSurface[this.surface].play(true);
            }
            this.relPos = (this.x - road.left) / this.laneWidth;
            this.panAll();
            if (this.relPos < 0 || this.relPos > 1) {
                if (this.speed < this.topspeed / 2) this.miniCrash((road.right + road.left) / 2);
                else this.crash();
            } else if ((this.relPos < 0.05 || this.relPos > 0.95) && this.speed > this.topspeed / 10) {
                this.race.curbWarning();
            }
        } else if (this.state === State.Crashing) {
            this.x = (road.right + road.left) / 2;
        } else if (this.state === State.Stopped || this.state === State.Starting) {
            this.relPos = (this.x - road.left) / this.laneWidth;
            this.panAll();
        }
    }

    // Your own car is heard from where it sits across the road: centred in the lane, panned
    // towards an edge as you drift to it (the original's squared pan curve).
    panAll() {
        const d = this.relPos - 0.5;
        const pan = d < 0 ? d * d * -100 * 4 : d * d * 100 * 4;
        const p = Math.max(-100, Math.min(100, pan));
        for (const s of [this.sEngine, this.sStart, this.sThrottle, this.sHorn, this.sBrake, this.sBackfire, this.sWipers, this.sSurface[this.surface]])
            if (s) s.pan(p);
    }

    dispose() {
        for (const s of this.allSounds()) s.dispose();
    }
}

export class Bot extends Vehicle {
    constructor(race, vehicle, playerNumber, difficulty) {
        super(race, vehicle);
        this.playerNumber = playerNumber;
        this.difficulty = difficulty;
        this.random = rnd(100);
        this.horning = false;
        this.currentSteering = 0;
        this.currentThrottle = 0;
        this.currentBrake = 0;
        this.laneWidth = race.track.laneWidth;
    }

    soundPaths() {
        const p = n => vehicleSoundPath(this.v, n);
        return [p('engine'), p('start'), p('horn'), p('crash'), p('brake'), p('backfire'), 'Legacy/bump.wav', 'Legacy/crashshort.wav'];
    }

    createSounds() {
        const a = this.race.audio;
        const p = n => vehicleSoundPath(this.v, n);
        this.sEngine = a.create(p('engine'), 'others');
        this.sStart = a.create(p('start'), 'others');
        this.sHorn = a.create(p('horn'), 'others');
        this.sCrash = a.create(p('crash'), 'others');
        this.sBrake = a.create(p('brake'), 'others');
        this.sBackfire = p('backfire') ? a.create(p('backfire'), 'others') : null;
        this.sBump = a.create('Legacy/bump.wav', 'others');
        this.sMiniCrash = a.create('Legacy/crashshort.wav', 'others');
        this.sounds = [this.sEngine, this.sStart, this.sHorn, this.sCrash, this.sBrake, this.sBackfire, this.sBump, this.sMiniCrash].filter(Boolean);
        this.surface = this.race.track.segments[0].surface;
    }

    pendingStart() {
        this.push('computerStart', 1.5 + 3 * rnd(100) / 100);
    }

    start() {
        this.push('carStart', Math.max(0.05, this.sStart.length - 0.1));
        this.sStart.play();
        this.speed = 0;
        this.frequency = this.idlefreq;
        this.state = State.Starting;
    }

    crash(newX) {
        this.speed = 0;
        this.sCrash.play();
        this.sEngine.stop();
        this.sBrake.stop();
        this.sHorn.stop();
        this.gear = 1;
        this.x = newX;
        this.state = State.Crashing;
        this.push('carRestart', this.sCrash.length + 1.25);
    }

    miniCrash(newX) {
        this.speed /= 4;
        this.x = newX;
        this.sMiniCrash.play();
    }

    bump(bumpX, bumpY, bumpSpeed) {
        if (bumpY !== 0) {
            this.speed -= bumpSpeed;
            this.y += bumpY;
        }
        if (bumpX !== 0) {
            this.x += 2 * bumpX;
            this.speed -= this.speed / 5;
        }
        if (this.speed < 0) this.speed = 0;
        this.sBump.play();
    }

    stop() {
        this.state = State.Stopping;
        this.sBrake.stop();
        this.sHorn.stop();
    }

    run(dt, playerX, playerY) {
        const track = this.race.track;
        const len = track.lapDistance;
        const diffX = this.x - playerX;
        let diffY = this.y - playerY;
        diffY = ((diffY % len) + len) % len;
        if (diffY > len / 2) diffY = (diffY - len) % len;
        this.diffY = diffY;

        if (!this.horning && diffY < -10000 && this.state === State.Running && rnd(2500) === 1) {
            this.horning = true;
            this.push('stopHorn', 0.2 + rnd(80) / 80);
        }

        // Positional sound (the original's 2D mode): pan by lateral offset, fade with distance.
        const rx = diffX / this.laneWidth;
        const ry = diffY / 12000;
        const distance = Math.sqrt(Math.abs(rx) + Math.abs(ry));
        const pan = rx < -2 ? -100 : rx > 2 ? 100 : rx * 50;
        const vol = this.finished ? Math.max(0, 100 - distance * 10 - 20) : 100 - distance * 10;
        for (const s of this.sounds) { s.pan(pan); s.volume(vol); }

        if (this.state === State.Running && this.race.started) {
            this.ai();
            const [accel, decel] = surfaceFactors(this.surface, this.acceleration, this.deceleration, true);
            let thrust = this.currentThrottle;
            if (this.currentThrottle === 0) {
                thrust = this.currentBrake;
                if (this.currentBrake !== 0) {
                    if (this.surface === 'Asphalt' && !this.sBrake.playing) this.sBrake.play();
                    else if (this.surface !== 'Asphalt') this.sBrake.stop();
                }
            } else if (this.currentBrake === 0) {
                if (this.sBrake.playing) this.sBrake.stop();
            } else if (-this.currentBrake > this.currentThrottle) {
                thrust = this.currentBrake;
            }
            let speedDiff;
            if (thrust > 10) speedDiff = dt * thrust * accel;
            else if (thrust < -10) speedDiff = dt * thrust * decel;
            else speedDiff = dt * -1000;
            if (speedDiff > 0) speedDiff *= 2 - (this.topspeed + this.speed) / (2 * this.topspeed);
            this.speed = Math.max(0, Math.min(this.topspeed, this.speed + speedDiff));
            if (thrust < -50 && this.speed > 5000) this.currentSteering = this.currentSteering * 2 / 3;
            this.y += this.speed * dt * TIME_SCALE;
            const steerMul = this.surface === 'Snow' ? this.steering * 1.44 : this.steering;
            this.x += this.currentSteering * dt * TIME_SCALE * steerMul * ((5000 + this.speed * this.steeringFactor / 100) / this.topspeed);
            this.sBrake.frequency(11025 + 22050 * this.speed / this.topspeed);
            if (this.autoEngineFrequency() && this.sBackfire) {
                if (!this.backfireAuto && rnd(5) === 1 && !this.sBackfire.playing) this.sBackfire.play();
                this.backfireAuto = true;
            } else this.backfireAuto = false;
            this.sEngine.frequency(this.frequency);
            if (!this.finished) this.evaluate(track.roadAt(this.y));
        } else if (this.state === State.Stopping) {
            this.speed = Math.max(0, this.speed - dt * 100 * this.deceleration);
            this.y += this.speed * dt * TIME_SCALE;
            this.autoEngineFrequency();
            this.sEngine.frequency(this.frequency);
            if (this.speed === 0 && this.sEngine.playing) this.sEngine.stop();
        }

        if (this.horning && this.state === State.Running) {
            if (!this.sHorn.playing) this.sHorn.play(true);
        } else if (this.sHorn.playing) {
            this.sHorn.stop();
        }

        for (const e of this.takeEvents()) {
            switch (e.type) {
                case 'computerStart':
                case 'carRestart':
                    this.start();
                    break;
                case 'carStart':
                    this.frequency = this.idlefreq;
                    this.sEngine.frequency(this.frequency);
                    this.sEngine.play(true);
                    this.state = State.Running;
                    break;
                case 'stopHorn':
                    this.horning = false;
                    break;
            }
        }
    }

    evaluate(road) {
        const relPos = (this.x - road.left) / (this.laneWidth * 2);
        if (relPos < 0 || relPos > 1) {
            if (this.speed < this.topspeed / 2) this.miniCrash((road.right + road.left) / 2);
            else this.crash((road.right + road.left) / 2);
        }
        this.surface = road.surface;
    }

    // The original computer driver: full throttle, steer back toward the middle, and slow for
    // hairpins. Difficulty changes how early and how hard it reacts.
    ai() {
        const track = this.race.track;
        const road = track.roadAt(this.y);
        const relPos = (this.x - road.left) / (this.laneWidth * 2);
        const next = track.roadAt(this.y + 3000);
        const d = this.difficulty;
        const r = this.random;
        this.currentThrottle = 100;
        this.currentSteering = 0;
        this.currentBrake = 0;
        if (road.type === 'HairpinLeft' || next.type === 'HairpinLeft') {
            if (relPos > (d === 0 ? 0.65 : 0.55)) this.currentSteering = -100;
            if (d === 1) this.currentThrottle = 66;
            if (d === 2) this.currentThrottle = 33;
        } else if (road.type === 'HairpinRight' || next.type === 'HairpinRight') {
            if (relPos < (d === 0 ? 0.35 : 0.45)) this.currentSteering = 100;
            if (d === 1) this.currentThrottle = 66;
            if (d === 2) this.currentThrottle = 33;
        } else if (relPos < 0.4) {
            if (relPos > 0.2) this.currentSteering = 100 - r / [5, 10, 25][d];
            else {
                this.currentSteering = [100 - r / 10, 100 - r / 20, 100][d];
                if (d === 1) this.currentThrottle = 75;
                if (d === 2) this.currentThrottle = 50;
            }
        } else if (relPos > 0.6) {
            if (relPos < 0.8) this.currentSteering = -100 + r / [5, 10, 25][d];
            else {
                this.currentSteering = [-100 + r / 10, -100 + r / 20, -100][d];
                if (d === 1) this.currentThrottle = 75;
                if (d === 2) this.currentThrottle = 50;
            }
        }
    }

    dispose() {
        for (const s of this.sounds) s.dispose();
    }
}
