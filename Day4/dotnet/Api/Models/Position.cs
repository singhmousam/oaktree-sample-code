namespace OakTree.Api.Models;

public enum Side
{
    BUY,
    SELL
}

/// <summary>Inbound request body for booking a new position.</summary>
public record PositionIn(string Symbol, int Quantity, Side Side);

/// <summary>A booked position, as returned by the API.</summary>
public record Position(string Id, string Symbol, int Quantity, Side Side, DateTime BookedAt)
{
    public static Position From(PositionIn input) =>
        new(Guid.NewGuid().ToString(), input.Symbol, input.Quantity, input.Side, DateTime.UtcNow);
}
