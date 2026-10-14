use std::collections::BTreeMap;
use std::fs;
use std::path::Path;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Supply {
    pub name: String,
    pub kind: String,
    pub present: Option<bool>,
    pub scope: Option<String>,
    pub online: Option<bool>,
    pub attributes: BTreeMap<String, String>,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct PowerSnapshot {
    pub supplies: Vec<Supply>,
}

impl PowerSnapshot {
    pub fn has_battery(&self) -> bool {
        self.battery_supply().is_some()
    }

    pub fn has_external_power(&self) -> bool {
        self.supplies.iter().any(|supply| {
            matches!(supply.kind.as_str(), "Mains" | "USB") && supply.online == Some(true)
        })
    }

    pub fn on_battery(&self) -> bool {
        self.has_battery() && !self.has_external_power()
    }

    pub fn battery_supply(&self) -> Option<&Supply> {
        self.supplies
            .iter()
            .filter(|supply| {
                supply.kind == "Battery"
                    && supply.present == Some(true)
                    && supply.scope.as_deref() != Some("Device")
            })
            .min_by(|left, right| left.name.cmp(&right.name))
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum BatteryState {
    Charging,
    Discharging,
    FullyCharged,
    PendingCharge,
    Unknown,
}

impl BatteryState {
    fn from_status(status: &str) -> Self {
        match status {
            "Charging" => Self::Charging,
            "Discharging" => Self::Discharging,
            "Full" => Self::FullyCharged,
            "Not charging" => Self::PendingCharge,
            _ => Self::Unknown,
        }
    }

    fn label(self) -> &'static str {
        match self {
            Self::Charging => "charging",
            Self::Discharging => "discharging",
            Self::FullyCharged => "fully-charged",
            Self::PendingCharge => "pending-charge",
            Self::Unknown => "unknown",
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct BatteryReport {
    pub percentage: i64,
    pub state: BatteryState,
    pub rate_watts: f64,
    pub size_wh: i64,
    pub time_minutes: Option<i64>,
    pub cycles: Option<i64>,
    pub start_threshold: Option<i64>,
    pub end_threshold: Option<i64>,
    pub holding: bool,
}

impl BatteryReport {
    pub fn from_supply(supply: &Supply, external_power: bool) -> Result<Self, String> {
        let percentage = required_integer(supply, "capacity")?;
        let state = BatteryState::from_status(
            supply
                .attributes
                .get("status")
                .map(String::as_str)
                .unwrap_or(""),
        );
        let rate_watts = rate_watts(supply);
        let size_wh = size_wh(supply);
        let time_minutes = time_minutes(supply, state, rate_watts);
        let start_threshold = integer(supply, "charge_control_start_threshold");
        let end_threshold = integer(supply, "charge_control_end_threshold");
        let cycles = integer(supply, "cycle_count");
        let holding = is_holding(external_power, state, percentage, rate_watts, end_threshold);

        Ok(Self {
            percentage,
            state,
            rate_watts,
            size_wh,
            time_minutes,
            cycles,
            start_threshold,
            end_threshold,
            holding,
        })
    }

    pub fn threshold_label(&self) -> Option<String> {
        let end = self.end_threshold?;
        match self.start_threshold {
            Some(start) if start != end => Some(format!("{start}-{end}%")),
            _ => Some(format!("{end}%")),
        }
    }

    pub fn rate_text(&self) -> String {
        let rounded = format!("{:.1}", self.rate_watts.abs());
        let number = rounded.strip_suffix(".0").unwrap_or(&rounded);
        format!("{number}W")
    }

    pub fn size_text(&self) -> String {
        format!("{}Wh", self.size_wh)
    }

    pub fn time_text(&self) -> Option<String> {
        let minutes = self.time_minutes?;
        let hours = minutes / 60;
        let remainder = minutes % 60;
        Some(if hours == 0 {
            format!("{remainder}m")
        } else if remainder == 0 {
            format!("{hours}h")
        } else {
            format!("{hours}h {remainder}m")
        })
    }

    pub fn fields_text(&self) -> String {
        let state = if self.holding {
            "holding"
        } else {
            self.state.label()
        };
        let mut fields = vec![
            format!("percentage\t{}%", self.percentage),
            format!("state\t{state}"),
            format!("rate\t{}", self.rate_text()),
            format!("size\t{}", self.size_text()),
            format!("time\t{}", self.time_text().unwrap_or_default()),
        ];
        if let Some(cycles) = self.cycles {
            fields.push(format!("cycles\t{cycles}"));
        }
        if let Some(threshold) = self.threshold_label() {
            fields.push(format!("threshold\t{threshold}"));
        }
        fields.join("\n") + "\n"
    }

    pub fn line_text(&self) -> String {
        if self.holding {
            let label = self.threshold_label().unwrap_or_default();
            format!(
                "Battery {}%  ·  Holding at {}  ·  {} / {}",
                self.percentage,
                label,
                self.rate_text(),
                self.size_text()
            )
        } else if let Some(time) = self.time_text() {
            let relation = if self.state == BatteryState::Charging {
                "to full"
            } else {
                "left"
            };
            format!(
                "Battery {}%  ·  {} {}  ·  {} / {}",
                self.percentage,
                time,
                relation,
                self.rate_text(),
                self.size_text()
            )
        } else {
            format!(
                "Battery {}%  ·  {} / {}",
                self.percentage,
                self.rate_text(),
                self.size_text()
            )
        }
    }
}

pub fn read_snapshot(root: &Path) -> Result<PowerSnapshot, String> {
    let entries =
        fs::read_dir(root).map_err(|error| format!("could not read power supplies: {error}"))?;
    let mut paths = entries
        .filter_map(Result::ok)
        .map(|entry| entry.path())
        .collect::<Vec<_>>();
    paths.sort_by(|left, right| left.file_name().cmp(&right.file_name()));

    let mut supplies = Vec::new();
    for path in paths {
        let Some(name) = path.file_name().and_then(|name| name.to_str()) else {
            continue;
        };
        let Some(kind) = read_text(&path, "type") else {
            continue;
        };
        let mut attributes = BTreeMap::new();
        for attribute in [
            "capacity",
            "status",
            "power_now",
            "current_now",
            "voltage_now",
            "energy_full",
            "energy_now",
            "charge_full",
            "charge_now",
            "voltage_min_design",
            "charge_control_start_threshold",
            "charge_control_end_threshold",
            "cycle_count",
        ] {
            if let Some(value) = read_text(&path, attribute) {
                attributes.insert(attribute.to_string(), value);
            }
        }
        supplies.push(Supply {
            name: name.to_string(),
            kind,
            present: read_bool(&path, "present"),
            scope: read_text(&path, "scope"),
            online: read_bool(&path, "online"),
            attributes,
        });
    }
    Ok(PowerSnapshot { supplies })
}

fn read_text(root: &Path, name: &str) -> Option<String> {
    fs::read_to_string(root.join(name))
        .ok()
        .map(|value| value.trim().to_string())
}

fn read_bool(root: &Path, name: &str) -> Option<bool> {
    match read_text(root, name)?.as_str() {
        "1" => Some(true),
        "0" => Some(false),
        _ => None,
    }
}

fn integer(supply: &Supply, name: &str) -> Option<i64> {
    supply.attributes.get(name)?.parse().ok()
}

fn required_integer(supply: &Supply, name: &str) -> Result<i64, String> {
    integer(supply, name).ok_or_else(|| format!("could not read battery {name}"))
}

fn rate_watts(supply: &Supply) -> f64 {
    if let Some(power) = integer(supply, "power_now") {
        return power.unsigned_abs() as f64 / 1_000_000.0;
    }
    let Some(current) = integer(supply, "current_now") else {
        return 0.0;
    };
    let Some(voltage) = integer(supply, "voltage_now") else {
        return 0.0;
    };
    (current.unsigned_abs() as f64 * voltage.unsigned_abs() as f64) / 1_000_000_000_000.0
}

fn size_wh(supply: &Supply) -> i64 {
    if let Some(energy) = integer(supply, "energy_full") {
        return energy / 1_000_000;
    }
    match (
        integer(supply, "charge_full"),
        integer(supply, "voltage_min_design"),
    ) {
        (Some(charge), Some(voltage)) => {
            ((charge as i128 * voltage as i128) / 1_000_000_000_000) as i64
        }
        _ => 0,
    }
}

fn time_minutes(supply: &Supply, state: BatteryState, rate_watts: f64) -> Option<i64> {
    if !matches!(state, BatteryState::Charging | BatteryState::Discharging) || rate_watts <= 0.0 {
        return None;
    }
    let scale = if state == BatteryState::Charging {
        1
    } else {
        -1
    };
    let remaining_energy = match (
        integer(supply, "energy_full"),
        integer(supply, "energy_now"),
    ) {
        (Some(full), Some(now)) => Some(if scale > 0 { full - now } else { now }),
        _ => None,
    };
    if let Some(energy) = remaining_energy {
        if energy < 0 {
            return None;
        }
        let rate_microwatts = rate_watts * 1_000_000.0;
        return Some(((energy as f64 / rate_microwatts) * 60.0).floor() as i64);
    }

    let remaining_charge = match (
        integer(supply, "charge_full"),
        integer(supply, "charge_now"),
    ) {
        (Some(full), Some(now)) => Some(if scale > 0 { full - now } else { now }),
        _ => None,
    }?;
    let current = integer(supply, "current_now")?.unsigned_abs();
    if remaining_charge < 0 || current == 0 {
        return None;
    }
    Some(((remaining_charge as f64 / current as f64) * 60.0).floor() as i64)
}

fn is_holding(
    external_power: bool,
    state: BatteryState,
    percentage: i64,
    rate_watts: f64,
    end_threshold: Option<i64>,
) -> bool {
    let Some(end) = end_threshold else {
        return false;
    };
    if !external_power {
        return false;
    }
    match state {
        BatteryState::PendingCharge => true,
        BatteryState::FullyCharged => percentage < 99,
        BatteryState::Charging => rate_watts <= 0.2 && end < 99 && percentage >= end,
        BatteryState::Discharging | BatteryState::Unknown => false,
    }
}
