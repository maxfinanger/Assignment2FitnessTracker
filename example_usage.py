"""Minimal demonstration of the instructor-supplied fitness data generator."""

from data_generator import available_scenarios, generate_fitness_data


def main():
    print("Available scenarios:", available_scenarios())

    profile, observations = generate_fitness_data(
        participant_id="P001",
        scenario="recovery",
        seed=42,
        number_of_windows=10,
    )

    print("\nParticipant profile")
    print(profile)
    print("\nFirst three observations")
    for observation in observations[:3]:
        print(observation)

    # Your program should convert these dictionaries into your own objects,
    # validate them, analyze the complete session, and produce a report.


if __name__ == "__main__":
    main()

