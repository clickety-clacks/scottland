#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Step {
    Increase(u8),
    Decrease(u8),
    Set(u8),
}

/// Calculate the DS3 target for a `+N%`, `N%-`, or `N%` brightness step.
///
/// Returns `None` when the step does not match the contract's 1–100 integer form.
pub fn brightness_target_percent(current_percent: u8, step: &str) -> Option<u8> {
    let step = parse_step(step)?;
    let current = i16::from(current_percent);
    let amount = match step {
        Step::Increase(amount) => i16::from(amount),
        Step::Decrease(amount) => i16::from(amount),
        Step::Set(amount) => i16::from(amount),
    };

    let target = match step {
        Step::Increase(5) if current < 5 => current + 1,
        Step::Increase(_) => current + amount,
        Step::Decrease(5) if current <= 5 => current - 1,
        Step::Decrease(_) => current - amount,
        Step::Set(_) => amount,
    };

    Some(target.clamp(1, 100) as u8)
}

fn parse_step(step: &str) -> Option<Step> {
    if let Some(value) = step.strip_prefix('+') {
        let value = value.strip_suffix('%')?;
        return parse_amount(value).map(Step::Increase);
    }
    if let Some(value) = step.strip_suffix("%-") {
        return parse_amount(value).map(Step::Decrease);
    }
    if let Some(value) = step.strip_suffix('%') {
        return parse_amount(value).map(Step::Set);
    }
    None
}

fn parse_amount(value: &str) -> Option<u8> {
    if value.is_empty() || !value.bytes().all(|byte| byte.is_ascii_digit()) {
        return None;
    }
    let amount = value.parse::<u8>().ok()?;
    (1..=100).contains(&amount).then_some(amount)
}

#[cfg(test)]
mod tests {
    use super::brightness_target_percent;

    #[test]
    fn parses_increase_decrease_and_absolute_steps() {
        assert_eq!(brightness_target_percent(60, "+5%"), Some(65));
        assert_eq!(brightness_target_percent(60, "5%-"), Some(55));
        assert_eq!(brightness_target_percent(60, "42%"), Some(42));
    }

    #[test]
    fn uses_the_requested_amount_for_increase_and_decrease() {
        assert_eq!(brightness_target_percent(30, "+12%"), Some(42));
        assert_eq!(brightness_target_percent(30, "12%-"), Some(18));
    }

    #[test]
    fn five_percent_increase_has_one_point_steps_below_five_only() {
        assert_eq!(brightness_target_percent(4, "+5%"), Some(5));
        assert_eq!(brightness_target_percent(5, "+5%"), Some(10));
    }

    #[test]
    fn five_percent_decrease_has_one_point_steps_at_five_or_below() {
        assert_eq!(brightness_target_percent(5, "5%-"), Some(4));
        assert_eq!(brightness_target_percent(6, "5%-"), Some(1));
    }

    #[test]
    fn clamps_every_target_to_one_through_one_hundred() {
        assert_eq!(brightness_target_percent(98, "+5%"), Some(100));
        assert_eq!(brightness_target_percent(100, "+5%"), Some(100));
        assert_eq!(brightness_target_percent(1, "5%-"), Some(1));
        assert_eq!(brightness_target_percent(2, "5%-"), Some(1));
        assert_eq!(brightness_target_percent(0, "1%"), Some(1));
        assert_eq!(brightness_target_percent(100, "100%"), Some(100));
    }

    #[test]
    fn rejects_steps_outside_the_contract_forms_and_range() {
        for step in ["5", "+0%", "0%-", "101%", "-5%", "5%+", "+5%-"] {
            assert_eq!(brightness_target_percent(50, step), None, "{step}");
        }
    }

    #[test]
    fn rejects_repeated_plus_before_parsing_the_amount() {
        assert_eq!(brightness_target_percent(50, "++5%"), None);
    }
}
